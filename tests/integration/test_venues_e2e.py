"""End-to-end recorder tests for Bybit (linear) and Deribit against protocol fakes."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from quanta.core.clock import LiveClock
from quanta.recorder.bybit_linear import BybitLinearCapture
from quanta.recorder.config import RecorderConfig
from quanta.recorder.deribit import DeribitCapture
from quanta.recorder.segment import PARTIAL_SUFFIX, SEGMENT_SUFFIX
from quanta.recorder.service import RecorderService
from quanta.tools.raw_reader import iter_records
from tests.integration.fake_venues import FakeBybit, FakeDeribit

SYMS = ["BTCUSDT", "ETHUSDT"]
INSTR = ["BTC-PERPETUAL", "ETH-PERPETUAL"]


async def wait_for(pred: Any, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.05)


def cfg_for(tmp_path: Path, **venues: Any) -> RecorderConfig:
    return RecorderConfig.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "min_free_disk_gb": 0,
            "binance_usdm": {"enabled": False},
            **venues,
            "segments": {"flush_interval_s": 0.2, "fsync": False},
            "ws": {"backoff_max_s": 0.3, "stable_after_s": 0.5, "idle_timeout_s": 5},
            "metrics": {"enabled": False},
        }
    )


def metas(data_dir: Path, venue: str) -> list[dict[str, Any]]:
    files = sorted((data_dir / "raw" / venue / "meta").rglob(f"*{SEGMENT_SUFFIX}"))
    return [json.loads(bytes(r.p)) for r in iter_records(files) if r.k == "meta"]


def gauge(svc: RecorderService, name: str, **labels: str) -> float | None:
    return svc.metrics.registry.get_sample_value(name, labels)


@pytest.fixture
async def bybit() -> AsyncIterator[FakeBybit]:
    f = FakeBybit(SYMS)
    await f.start()
    yield f
    await f.stop()


@pytest.fixture
async def deribit() -> AsyncIterator[FakeDeribit]:
    f = FakeDeribit(INSTR)
    await f.start()
    yield f
    await f.stop()


async def test_bybit_end_to_end(tmp_path: Path, bybit: FakeBybit) -> None:
    cfg = cfg_for(
        tmp_path,
        bybit_linear={
            "enabled": True,
            "ws_url": bybit.ws_url,
            "rest_url": bybit.rest_url,
            "book_symbols": SYMS,
            "universe": SYMS,
            "ping_interval_s": 0.2,
        },
    )
    svc = RecorderService(cfg, LiveClock())
    stop = asyncio.Event()
    task = asyncio.create_task(svc.run(stop))

    def synced(s: str) -> bool:
        return gauge(svc, "quanta_recorder_depth_synced", venue="bybit_linear", symbol=s) == 1.0

    await wait_for(lambda: all(synced(s) for s in SYMS))
    await wait_for(lambda: bybit.pings >= 2)  # app-level ping keeps the socket alive
    bybit.restart("BTCUSDT")  # u=1 snapshot after a service restart
    await asyncio.sleep(0.3)
    await bybit.close_all_ws()  # reconnect → resubscribe → snapshots
    await wait_for(lambda: bybit.ws_connects >= 4)
    await wait_for(lambda: all(synced(s) for s in SYMS))
    await asyncio.sleep(0.5)
    bybit.paused = True  # freeze truth, drain in-flight frames
    await asyncio.sleep(0.4)
    cap = svc.captures["bybit_linear"]
    assert isinstance(cap, BybitLinearCapture)
    for s in SYMS:
        book = cap.books[s].book
        assert book is not None
        truth_bids, truth_asks = bybit.books[s]
        assert book.bids == truth_bids and book.asks == truth_asks, s
    stop.set()
    await asyncio.wait_for(task, 10)

    data = cfg.data_dir
    assert not list(data.rglob(f"*{PARTIAL_SUFFIX}"))
    types = [m["type"] for m in metas(data, "bybit_linear")]
    assert "book_reset" in types and "depth_reset" in types and "capture_start" in types
    # allLiquidation is complete: every liquidation the exchange sent was received
    received = (
        gauge(svc, "quanta_recorder_liquidations_total", venue="bybit_linear", market="linear") or 0
    )
    # every liquidation delivered by the exchange was counted (complete feed, no drops)
    assert received == sum(bybit.liquidations_sent.values()) > 0


async def test_bybit_book_error_forces_resubscribe(tmp_path: Path, bybit: FakeBybit) -> None:
    cfg = cfg_for(
        tmp_path,
        bybit_linear={
            "enabled": True,
            "ws_url": bybit.ws_url,
            "rest_url": bybit.rest_url,
            "book_symbols": SYMS,
            "universe": SYMS,
        },
    )
    svc = RecorderService(cfg, LiveClock())
    stop = asyncio.Event()
    task = asyncio.create_task(svc.run(stop))
    await wait_for(
        lambda: (
            gauge(svc, "quanta_recorder_depth_synced", venue="bybit_linear", symbol="BTCUSDT")
            == 1.0
        )
    )
    connects = bybit.ws_connects
    bybit.bad_price_once.add("BTCUSDT")
    await wait_for(lambda: bybit.ws_connects > connects)
    await wait_for(
        lambda: (
            gauge(svc, "quanta_recorder_depth_synced", venue="bybit_linear", symbol="BTCUSDT")
            == 1.0
        )
    )
    stop.set()
    await asyncio.wait_for(task, 10)
    assert any(m["type"] == "book_error" for m in metas(cfg.data_dir, "bybit_linear"))


async def test_deribit_end_to_end(tmp_path: Path, deribit: FakeDeribit) -> None:
    cfg = cfg_for(
        tmp_path,
        deribit={
            "enabled": True,
            "ws_url": deribit.ws_url,
            "rest_url": deribit.rest_url,
            "instruments": INSTR,
            "volatility_indices": ["btc_usd"],
            "heartbeat_s": 10,
        },
    )
    svc = RecorderService(cfg, LiveClock())
    stop = asyncio.Event()
    task = asyncio.create_task(svc.run(stop))

    def synced(i: str) -> bool:
        return gauge(svc, "quanta_recorder_depth_synced", venue="deribit", symbol=i) == 1.0

    await wait_for(lambda: all(synced(i) for i in INSTR))
    await wait_for(lambda: deribit.test_replies >= 3)  # heartbeats answered
    deribit.drop_change.add("BTC-PERPETUAL")  # gap in the change_id chain
    await wait_for(lambda: deribit.unsubscribes >= 1)  # book-only resubscription
    await wait_for(lambda: synced("BTC-PERPETUAL"))
    await asyncio.sleep(0.5)
    deribit.paused = True
    await asyncio.sleep(0.4)
    cap = svc.captures["deribit"]
    assert isinstance(cap, DeribitCapture)
    for i in INSTR:
        book = cap.books[i].book
        assert book is not None
        tb, ta = deribit.books[i]
        assert book.bids == {5 * p: 10 * q for p, q in tb.items()}, i
        assert book.asks == {5 * p: 10 * q for p, q in ta.items()}, i
    stop.set()
    await asyncio.wait_for(task, 10)

    assert deribit.heartbeat_kills == 0 and deribit.ws_connects == 1
    ms = metas(cfg.data_dir, "deribit")
    assert any(m["type"] == "depth_gap" and m["symbol"] == "BTC-PERPETUAL" for m in ms)
    assert not any(m["type"] == "trade_gap" for m in ms), "trades unaffected by book resync"
    liq = gauge(svc, "quanta_recorder_liquidations_total", venue="deribit", market="T") or 0
    assert liq == sum(deribit.liquidations_sent.values()) > 0  # complete liquidation feed
    raw_book = sorted(
        (cfg.data_dir / "raw" / "deribit" / "public_book").rglob(f"*{SEGMENT_SUFFIX}")
    )
    assert raw_book and all(r.k == "ws" for r in iter_records(raw_book))


async def test_deribit_unanswered_heartbeat_is_detected_by_server(
    tmp_path: Path, deribit: FakeDeribit
) -> None:
    """Sanity check of the fake: a client that ignores test_request gets disconnected —
    so test_deribit_end_to_end passing proves the recorder answers heartbeats."""
    import aiohttp

    async with aiohttp.ClientSession() as s, s.ws_connect(deribit.ws_url) as ws:
        await ws.send_str(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "public/set_heartbeat",
                    "params": {"interval": 10},
                }
            )
        )
        await wait_for(lambda: deribit.heartbeat_kills == 1, timeout=3)
