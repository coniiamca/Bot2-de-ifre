"""End-to-end: RecorderService against the fake exchange, with injected faults."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from quanta.core.clock import LiveClock
from quanta.recorder.config import RecorderConfig
from quanta.recorder.segment import PARTIAL_SUFFIX, SEGMENT_SUFFIX, SegmentWriter, manifest_path
from quanta.recorder.service import RecorderService
from quanta.tools.book_audit import audit
from quanta.tools.raw_reader import iter_records
from quanta.tools.verify_aggtrades import TradeRow, compare, read_recorded
from tests.integration.fake_binance import FakeBinance

SYMBOLS = ["BTCUSDT", "ETHUSDT"]


@pytest.fixture
async def fake() -> AsyncIterator[FakeBinance]:
    f = FakeBinance(SYMBOLS)
    await f.start()
    yield f
    await f.stop()


def make_config(tmp_path: Path, fake: FakeBinance) -> RecorderConfig:
    return RecorderConfig.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "min_free_disk_gb": 0,
            "binance_usdm": {
                "depth_symbols": SYMBOLS,
                "universe": SYMBOLS,
                "snapshot_limit": 1000,
                "rest_url": fake.rest_url,
                "ws_url": fake.ws_url,
                "pollers": {
                    "server_time_s": 0.5,
                    "exchange_info_s": 5,
                    "funding_info_s": 5,
                    "premium_index_s": 1,
                    "open_interest_s": 1,
                    "stats_s": 2,
                    "insurance_balance_s": 5,
                    "depth_audit_s": 0.3,
                },
            },
            "segments": {"flush_interval_s": 0.2, "fsync": False},
            "ws": {"backoff_max_s": 0.3, "stable_after_s": 0.5, "idle_timeout_s": 5},
            "metrics": {"enabled": False},
        }
    )


async def wait_for(pred, timeout: float = 5.0) -> None:  # type: ignore[no-untyped-def]
    deadline = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.05)


def meta_records(data_dir: Path) -> list[dict]:  # type: ignore[type-arg]
    files = sorted((data_dir / "raw" / "binance_usdm" / "meta").rglob(f"*{SEGMENT_SUFFIX}"))
    return [json.loads(bytes(r.p)) for r in iter_records(files) if r.k == "meta"]


async def test_recorder_end_to_end_with_faults(tmp_path: Path, fake: FakeBinance) -> None:
    cfg = make_config(tmp_path, fake)
    svc = RecorderService(cfg, LiveClock())
    stop = asyncio.Event()
    task = asyncio.create_task(svc.run(stop))
    m = svc.metrics.registry

    def synced(sym: str) -> bool:
        v = m.get_sample_value(
            "quanta_recorder_depth_synced", {"venue": "binance_usdm", "symbol": sym}
        )
        return v == 1.0

    await wait_for(lambda: all(synced(s) for s in SYMBOLS))
    # 1) sequence gap: the exchange "loses" two BTC depth events
    fake.drop_depth["BTCUSDT"] = 2
    await wait_for(
        lambda: (
            (
                m.get_sample_value(
                    "quanta_recorder_depth_gaps_total",
                    {"venue": "binance_usdm", "symbol": "BTCUSDT", "reason": "pu_mismatch"},
                )
                or 0
            )
            >= 1
        )
    )
    await wait_for(lambda: synced("BTCUSDT"))
    # 2) all sockets dropped by the exchange
    await fake.close_all_ws()
    await wait_for(lambda: fake.ws_connects >= 6)  # 3 streams reconnected
    await wait_for(lambda: all(synced(s) for s in SYMBOLS))
    # 3) slow snapshot responses while resyncing (events keep flowing meanwhile)
    fake.snapshot_delay_s = 0.3
    fake.drop_depth["ETHUSDT"] = 1
    await asyncio.sleep(0.2)
    await wait_for(lambda: synced("ETHUSDT"))
    fake.snapshot_delay_s = 0.0
    await asyncio.sleep(2.0)  # let several audit snapshots land after the last resync
    stop.set()
    await asyncio.wait_for(task, 15)

    data_dir = cfg.data_dir
    assert not list(data_dir.rglob(f"*{PARTIAL_SUFFIX}")), "all segments finalized"
    segs = list(data_dir.rglob(f"*{SEGMENT_SUFFIX}"))
    assert {s.parent.parent.name for s in segs} >= {
        "public_depth",
        "public_bbo",
        "market",
        "rest",
        "meta",
    }
    assert all(manifest_path(s).exists() for s in segs)

    metas = meta_records(data_dir)
    types = [x["type"] for x in metas]
    for t in (
        "recorder_start",
        "capture_start",
        "depth_synced",
        "depth_gap",
        "depth_reset",
        "ws_lifecycle",
        "capture_stop",
        "recorder_stop",
    ):
        assert t in types, t
    assert (
        m.get_sample_value(
            "quanta_recorder_parse_errors_total", {"venue": "binance_usdm", "kind": "envelope"}
        )
        is None
    )

    today = datetime.now(UTC).date()
    for sym in SYMBOLS:
        # Reconstructed book matches every comparable REST snapshot.
        rep = audit(data_dir, sym, today)
        assert rep.ok, rep
        assert rep.compared >= 3 and rep.mismatched == 0
        # Every trade missing from the capture is explained by an in-band trade_gap record.
        recorded = read_recorded(data_dir, sym, today)
        truth = {
            t["a"]: TradeRow(
                t["a"], Decimal(t["p"]), Decimal(t["q"]), t["f"], t["l"], t["T"], t["m"]
            )
            for t in fake.state[sym].trades
        }
        cmp = compare(sym, today, truth, recorded)
        assert cmp.mismatched == 0 and cmp.extra_ids == 0
        explained = {
            i
            for x in metas
            if x["type"] == "trade_gap" and x["symbol"] == sym
            for i in range(x["first_missing"], x["last_missing"] + 1)
        }
        missing = {i for a, b in cmp.missing_ranges for i in range(a, b + 1)}
        assert missing <= explained


async def test_restricted_location_is_reported_and_survived(
    tmp_path: Path, fake: FakeBinance
) -> None:
    fake.restricted = True
    cfg = make_config(tmp_path, fake)
    svc = RecorderService(cfg, LiveClock())
    stop = asyncio.Event()
    task = asyncio.create_task(svc.run(stop))
    await wait_for(
        lambda: (
            svc.metrics.registry.get_sample_value(
                "quanta_recorder_restricted_location", {"venue": "binance_usdm"}
            )
            == 1.0
        )
    )
    await asyncio.sleep(0.5)
    stop.set()
    await asyncio.wait_for(task, 10)
    rest_files = sorted(
        (cfg.data_dir / "raw" / "binance_usdm" / "rest").rglob(f"*{SEGMENT_SUFFIX}")
    )
    statuses = {json.loads(bytes(r.p))["status"] for r in iter_records(rest_files)}
    assert 451 in statuses
    lifecycle = [x for x in meta_records(cfg.data_dir) if x["type"] == "ws_lifecycle"]
    assert any(x["event"] == "connect_failed" for x in lifecycle)


async def test_partial_segments_recovered_on_start(tmp_path: Path, fake: FakeBinance) -> None:
    cfg = make_config(tmp_path, fake)
    clock = LiveClock()
    crashed = SegmentWriter(cfg.data_dir, "binance_usdm", "market", clock, fsync=False)
    crashed.append(clock.now_ns(), b'{"t":1,"k":"ws","p":{}}')
    await crashed.flush()  # data on disk as .partial, never finalized ("crash")
    assert list(cfg.data_dir.rglob(f"*{PARTIAL_SUFFIX}"))
    svc = RecorderService(cfg, clock)
    stop = asyncio.Event()
    task = asyncio.create_task(svc.run(stop))
    await asyncio.sleep(0.5)
    stop.set()
    await asyncio.wait_for(task, 10)
    assert not list(cfg.data_dir.rglob(f"*{PARTIAL_SUFFIX}"))
    start = next(x for x in meta_records(cfg.data_dir) if x["type"] == "recorder_start")
    assert len(start["recovered_segments"]) == 1


async def test_unrepresentable_price_triggers_book_error_and_resync(
    tmp_path: Path, fake: FakeBinance
) -> None:
    cfg = make_config(tmp_path, fake)
    svc = RecorderService(cfg, LiveClock())
    stop = asyncio.Event()
    task = asyncio.create_task(svc.run(stop))
    m = svc.metrics.registry
    labels = {"venue": "binance_usdm", "symbol": "BTCUSDT"}
    await wait_for(lambda: m.get_sample_value("quanta_recorder_depth_synced", labels) == 1.0)
    fake.bad_price_once.add("BTCUSDT")
    await wait_for(
        lambda: (
            (
                m.get_sample_value(
                    "quanta_recorder_parse_errors_total",
                    {"venue": "binance_usdm", "kind": "depth_levels"},
                )
                or 0
            )
            >= 1
        )
    )
    await wait_for(
        lambda: (m.get_sample_value("quanta_recorder_depth_resyncs_total", labels) or 0) >= 2
    )
    await wait_for(lambda: m.get_sample_value("quanta_recorder_depth_synced", labels) == 1.0)
    stop.set()
    await asyncio.wait_for(task, 10)
    assert any(x["type"] == "book_error" for x in meta_records(cfg.data_dir))
