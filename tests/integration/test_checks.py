"""Daily checks end to end: a recording with a reconnect and a restart, verified against an
archive built from the fake exchange's ground truth (served from the checksum-verified cache)."""

from __future__ import annotations

import asyncio
import hashlib
import io
import zipfile
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from quanta.archive.binance_vision import archive_path
from quanta.core.clock import LiveClock
from quanta.lake.checks import CheckSettings, load_checks, pending_days, run_checks
from quanta.recorder.service import RecorderService
from quanta.tools.verify_aggtrades import ArchiveMissing, read_recorded
from tests.integration.fake_binance import FakeBinance
from tests.integration.test_recorder_e2e import make_config, wait_for

SYMBOLS = ["BTCUSDT", "ETHUSDT"]


@pytest.fixture
async def fake() -> AsyncIterator[FakeBinance]:
    f = FakeBinance(SYMBOLS)
    await f.start()
    yield f
    await f.stop()


def _archive_zip(trades: list[dict[str, Any]], tamper_id: int | None = None) -> bytes:
    lines = [
        "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker"
    ]
    for t in trades:
        price = "1234.5" if t["a"] == tamper_id else t["p"]
        lines.append(f"{t['a']},{price},{t['q']},{t['f']},{t['l']},{t['T']},{str(t['m']).lower()}")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("aggTrades.csv", "\n".join(lines) + "\n")
    return buf.getvalue()


def _cache(cache: Path, symbol: str, day: date, data: bytes) -> Path:
    path = cache / archive_path("aggTrades", symbol, "daily", day.isoformat())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    Path(str(path) + ".CHECKSUM").write_text(f"{hashlib.sha256(data).hexdigest()}  x.zip\n")
    return path


async def _record(tmp_path: Path, fake: FakeBinance) -> Path:
    cfg = make_config(tmp_path, fake)

    def synced(svc: RecorderService) -> bool:
        return all(
            svc.metrics.registry.get_sample_value(
                "quanta_recorder_depth_synced", {"venue": "binance_usdm", "symbol": s}
            )
            == 1.0
            for s in SYMBOLS
        )

    # run 1: the exchange drops every socket once (in-band trade_gap records)
    svc = RecorderService(cfg, LiveClock())
    stop = asyncio.Event()
    task = asyncio.create_task(svc.run(stop))
    await wait_for(lambda: synced(svc))
    await asyncio.sleep(1.0)
    await fake.close_all_ws()
    await wait_for(lambda: fake.ws_connects >= 6)
    await wait_for(lambda: synced(svc))
    await asyncio.sleep(1.0)
    stop.set()
    await asyncio.wait_for(task, 15)
    await asyncio.sleep(0.5)  # trades the recorder never sees: a restart
    # run 2
    svc = RecorderService(cfg, LiveClock())
    stop = asyncio.Event()
    task = asyncio.create_task(svc.run(stop))
    await wait_for(lambda: synced(svc))
    await asyncio.sleep(2.0)  # several audit snapshots
    stop.set()
    await asyncio.wait_for(task, 15)
    return cfg.data_dir


async def test_daily_checks_explain_restart_and_reconnect(
    tmp_path: Path, fake: FakeBinance
) -> None:
    data_dir = await _record(tmp_path, fake)
    day = datetime.now(UTC).date()
    cache = data_dir / "archive-cache"
    btc = _cache(cache, "BTCUSDT", day, _archive_zip(fake.state["BTCUSDT"].trades))
    settings = CheckSettings(["BTCUSDT", "ETHUSDT"], ["BTCUSDT", "ETHUSDT"], cache_dir=cache)

    def fetch(symbol: str, d: date, c: Path) -> tuple[Path, str]:
        if symbol == "ETHUSDT":
            raise ArchiveMissing("not yet")
        from quanta.tools.verify_aggtrades import fetch_archive

        return fetch_archive(symbol, d, c)  # served from the verified cache: no network

    # the scheduler runs the checks in a worker thread (they download with their own loop)
    doc = await asyncio.to_thread(run_checks, data_dir, day, settings, fetch=fetch)
    assert load_checks(data_dir, day) == doc
    t = doc["trades"]["BTCUSDT"]
    assert t["status"] == "explained", t
    assert t["mismatched"] == 0 and t["extra"] == 0 and t["unexplained"] == 0
    assert t["missing"] > 0
    assert t["explained"].get("restart", 0) > 0, t  # the gap between the two runs
    # a fast reconnect may or may not lose trades; if it does, they are explained too
    assert set(t["explained"]) <= {"restart", "trade_gap", "reconnect"}, t
    assert not btc.exists(), "the archive is deleted after use"
    assert doc["trades"]["ETHUSDT"]["status"] == "waiting"
    for s in SYMBOLS:
        assert doc["book"][s]["status"] == "ok", doc["book"][s]
        assert doc["book"][s]["compared"] >= 2
    assert doc["status"] == "waiting"

    # next run: ETH's archive is out, but tampered with → a mismatch fails the day;
    # BTC's final result is kept (no second download)
    recorded_eth = sorted(read_recorded(data_dir, "ETHUSDT", day))
    tamper = recorded_eth[len(recorded_eth) // 2]
    _cache(cache, "ETHUSDT", day, _archive_zip(fake.state["ETHUSDT"].trades, tamper_id=tamper))
    doc = await asyncio.to_thread(run_checks, data_dir, day, settings)
    assert doc["trades"]["BTCUSDT"]["status"] == "explained"
    assert doc["trades"]["ETHUSDT"]["status"] == "failed"
    assert doc["trades"]["ETHUSDT"]["mismatched"] == 1
    assert doc["status"] == "failed"


async def test_disk_floor_skips_downloads(tmp_path: Path) -> None:
    day = date(2026, 9, 26)
    (tmp_path / "raw" / "binance_usdm" / "market" / day.isoformat()).mkdir(parents=True)
    settings = CheckSettings(["BTCUSDT"], [], min_free_bytes=20e9)

    def fetch(symbol: str, d: date, c: Path) -> tuple[Path, str]:
        raise AssertionError("must not download below the floor")

    doc = run_checks(tmp_path, day, settings, fetch=fetch, disk_free=lambda _: 20.5e9)
    assert doc["trades"]["BTCUSDT"]["status"] == "skipped"
    assert doc["status"] == "waiting"
    assert pending_days(tmp_path, date(2026, 9, 27), settings) == [day]
