"""Raw → lake normalization over a real multi-venue recording (fake exchanges)."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest

from quanta.core.clock import LiveClock
from quanta.lake.normalize import PartialDataError, normalize_day
from quanta.lake.table import partition_path
from quanta.lake.verify import verify_day
from quanta.recorder.config import RecorderConfig
from quanta.recorder.service import RecorderService
from tests.integration.fake_binance import FakeBinance
from tests.integration.fake_venues import FakeBybit, FakeDeribit


class Recording:
    def __init__(self, root: Path, day: Any, fakes: dict[str, Any]) -> None:
        self.root = root
        self.day = day
        self.fakes = fakes


async def _record(root: Path) -> Recording:
    fb, fy, fd = FakeBinance(["BTCUSDT"]), FakeBybit(["BTCUSDT"]), FakeDeribit(["BTC-PERPETUAL"])
    for f in (fb, fy, fd):
        await f.start()
    cfg = RecorderConfig.model_validate(
        {
            "data_dir": str(root),
            "min_free_disk_gb": 0,
            "binance_usdm": {
                "depth_symbols": ["BTCUSDT"],
                "universe": ["BTCUSDT"],
                "rest_url": fb.rest_url,
                "ws_url": fb.ws_url,
                "pollers": {
                    "server_time_s": 0.5,
                    "exchange_info_s": 5,
                    "funding_info_s": 1,
                    "funding_rate_s": 1,
                    "premium_index_s": 0.5,
                    "open_interest_s": 0.5,
                    "stats_s": 1,
                    "insurance_balance_s": 5,
                    "depth_audit_s": 0.5,
                },
            },
            "bybit_linear": {
                "enabled": True,
                "ws_url": fy.ws_url,
                "rest_url": fy.rest_url,
                "book_symbols": ["BTCUSDT"],
                "universe": ["BTCUSDT"],
            },
            "deribit": {
                "enabled": True,
                "ws_url": fd.ws_url,
                "rest_url": fd.rest_url,
                "instruments": ["BTC-PERPETUAL"],
                "volatility_indices": ["btc_usd"],
            },
            "segments": {"flush_interval_s": 0.2, "fsync": False},
            "ws": {"backoff_max_s": 0.3, "stable_after_s": 0.5},
            "metrics": {"enabled": False},
        }
    )
    svc = RecorderService(cfg, LiveClock())
    stop = asyncio.Event()
    task = asyncio.create_task(svc.run(stop))
    await asyncio.sleep(1.5)
    await fb.close_all_ws()  # reconnect → trade gap + depth reset in the recording
    await asyncio.sleep(1.0)  # let the book resync
    fb.drop_depth["BTCUSDT"] = 1  # then a sequence gap on a live book
    await asyncio.sleep(1.0)
    for f in (fb, fy, fd):
        f.paused = True
    await asyncio.sleep(0.4)
    stop.set()
    await asyncio.wait_for(task, 15)
    for f in (fb, fy, fd):
        await f.stop()
    return Recording(root, datetime.now(UTC).date(), {"binance": fb, "bybit": fy, "deribit": fd})


@pytest.fixture(scope="module")
def rec(tmp_path_factory: pytest.TempPathFactory) -> Recording:
    return asyncio.run(_record(tmp_path_factory.mktemp("lake") / "data"))


def read(rec: Recording, venue: str, table: str) -> pa.Table:
    return pq.read_table(partition_path(rec.root, venue, table, rec.day.isoformat()))


def test_normalize_all_venues_and_determinism(rec: Recording) -> None:
    shas: dict[str, dict[str, str]] = {}
    for venue in ("binance_usdm", "bybit_linear", "deribit"):
        m = normalize_day(rec.root, venue, rec.day)
        assert m.invalid_frames == 0 and m.schema_errors == {}, (venue, m.schema_errors)
        assert m.sources and all(s["sha256"] for s in m.sources)
        shas[venue] = {t: x["sha256"] for t, x in m.tables.items() if x["rows"]}
        assert {"trades", "meta_events"} <= set(shas[venue])
    # determinism: same raw input → byte-identical Parquet
    again = normalize_day(rec.root, "binance_usdm", rec.day)
    assert {t: x["sha256"] for t, x in again.tables.items() if x["rows"]} == shas["binance_usdm"]
    report = verify_day(rec.root, rec.day)
    assert report["ok"], report


def test_spilling_to_disk_gives_identical_parquet(
    rec: Recording, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quanta.lake import table as lt

    for venue in ("binance_usdm", "bybit_linear", "deribit"):
        ref = normalize_day(rec.root, venue, rec.day)
        monkeypatch.setattr(lt, "SPILL_ROWS", 40)  # a full day is ~10⁸ rows; here: many chunks
        monkeypatch.setattr(lt, "BUCKET_NS", 10**9)
        spilled = normalize_day(rec.root, venue, rec.day)
        monkeypatch.undo()
        assert spilled.tables == ref.tables, venue
        assert not (rec.root / "lake" / venue / f".spill-{rec.day.isoformat()}").exists()


def test_disk_guard_stops_normalization(rec: Recording, monkeypatch: pytest.MonkeyPatch) -> None:
    from quanta.lake import table as lt
    from quanta.lake.table import DiskGuardError

    monkeypatch.setattr(lt, "SPILL_ROWS", 40)
    with pytest.raises(DiskGuardError):
        normalize_day(
            rec.root, "binance_usdm", rec.day, min_free_bytes=5e9, disk_free=lambda _: 1e9
        )
    assert not (rec.root / "lake" / "binance_usdm" / f".spill-{rec.day.isoformat()}").exists()


def test_binance_tables(rec: Recording) -> None:
    normalize_day(rec.root, "binance_usdm", rec.day)
    trades = read(rec, "binance_usdm", "trades")
    ids = trades.column("agg_id").to_pylist()
    assert len(ids) == len(set(ids)), "overlap duplicates removed"
    truth = {t["a"] for t in rec.fakes["binance"].state["BTCUSDT"].trades}
    assert set(ids) <= truth
    assert set(trades.column("aggressor").to_pylist()) <= {"buy", "sell"}
    arrivals = trades.column("ts_arrival").cast(pa.int64()).to_pylist()
    assert arrivals == sorted(arrivals)
    book = read(rec, "binance_usdm", "book_deltas")
    purposes = set(pc.drop_null(book.column("snapshot_purpose")).to_pylist())
    assert {"depth_resync", "depth_audit"} <= purposes
    for t in ("bbo", "mark_price", "open_interest", "premium_index", "positioning", "instruments"):
        assert read(rec, "binance_usdm", t).num_rows > 0, t
    meta = read(rec, "binance_usdm", "meta_events")
    assert "depth_gap" in set(meta.column("type").to_pylist())


def test_complete_liquidation_tables(rec: Recording) -> None:
    normalize_day(rec.root, "bybit_linear", rec.day)
    normalize_day(rec.root, "deribit", rec.day)
    by = read(rec, "bybit_linear", "liquidations")
    assert by.num_rows == sum(rec.fakes["bybit"].liquidations_sent.values())
    assert set(by.column("completeness").to_pylist()) == {"full"}
    de = read(rec, "deribit", "liquidations")
    assert de.num_rows == sum(rec.fakes["deribit"].liquidations_sent.values())


def _replay(rows: list[dict[str, Any]], id_col: str, qty_col: str) -> dict[str, dict[float, float]]:
    book: dict[str, dict[float, float]] = {"bid": {}, "ask": {}}
    events: dict[int, list[dict[str, Any]]] = {}
    order: list[int] = []
    for r in rows:
        if r[id_col] not in events:
            order.append(r[id_col])
            events[r[id_col]] = []
        events[r[id_col]].append(r)
    for eid in order:
        ev = events[eid]
        if ev[0]["is_snapshot"]:
            book = {"bid": {}, "ask": {}}
        for r in ev:
            side = book[r["side"]]
            if r[qty_col] == 0:
                side.pop(r["price"], None)
            else:
                side[r["price"]] = r[qty_col]
    return book


def test_books_rebuilt_from_lake_match_exchange(rec: Recording) -> None:
    """The lake alone is sufficient to reconstruct the exact final order book."""
    normalize_day(rec.root, "deribit", rec.day)
    normalize_day(rec.root, "bybit_linear", rec.day)
    cols = ["change_id", "is_snapshot", "side", "price", "amount"]
    rows = read(rec, "deribit", "book_deltas").select(cols).to_pylist()
    rows.sort(key=lambda r: (r["change_id"], not r["is_snapshot"]))
    book = _replay(rows, "change_id", "amount")
    tb, ta = rec.fakes["deribit"].books["BTC-PERPETUAL"]
    assert book["bid"] == {p / 2: float(q * 10) for p, q in tb.items()}
    assert book["ask"] == {p / 2: float(q * 10) for p, q in ta.items()}
    cols = ["update_id", "is_snapshot", "side", "price", "qty"]
    rows = read(rec, "bybit_linear", "book_deltas").select(cols).to_pylist()
    rows.sort(key=lambda r: (r["update_id"], not r["is_snapshot"]))
    book = _replay(rows, "update_id", "qty")
    tb, ta = rec.fakes["bybit"].books["BTCUSDT"]
    assert book["bid"] == {p / 10: q / 1000 for p, q in tb.items()}
    assert book["ask"] == {p / 10: q / 1000 for p, q in ta.items()}


def test_partial_day_refused(tmp_path: Path) -> None:
    d = tmp_path / "raw" / "deribit" / "market" / "2026-09-24"
    d.mkdir(parents=True)
    (d / "deribit.market.20260924T10Z.1.jsonl.zst.partial").write_bytes(b"")
    with pytest.raises(PartialDataError):
        normalize_day(tmp_path, "deribit", datetime(2026, 9, 24, tzinfo=UTC).date())


def test_parquet_has_lineage_metadata(rec: Recording) -> None:
    normalize_day(rec.root, "binance_usdm", rec.day)
    md = pq.read_schema(
        partition_path(rec.root, "binance_usdm", "trades", rec.day.isoformat())
    ).metadata
    assert md[b"quanta.normalizer_version"] == b"1"
    assert len(md[b"quanta.sources_sha256"]) == len(hashlib.sha256().hexdigest())


def test_quality_report(rec: Recording) -> None:
    from quanta.lake.quality import quality_report

    for v in ("binance_usdm", "bybit_linear", "deribit"):
        normalize_day(rec.root, v, rec.day)
    q = quality_report(rec.root, rec.day, ["binance_usdm", "bybit_linear", "deribit"])
    b = q["binance_usdm"]
    # the recording had a forced disconnect and an injected depth gap → degraded, not bad
    assert b["flag"] == "degraded"
    assert b["events"]["depth_gap"] >= 1 and b["events"]["depth_reset"] >= 1
    assert all(0.5 < c <= 1.0 for c in b["streams_connected_fraction"].values())
    assert b["trade_latency"]["BTCUSDT"]["trades"] > 0
    assert b["schema_errors"] == {} and b["invalid_frames"] == 0
    # untouched venues: no integrity events. Coverage is measured from recorder start, so
    # the connection handshake counts as uncovered: ≈99.7 % in this 4 s recording, and on a
    # slow CI runner it can fall below 99 % (→ "bad"). Over a real day it is negligible.
    # So check the flag follows the coverage rule instead of pinning it.
    for v in ("deribit", "bybit_linear"):
        assert q[v]["events"] == {} and q[v]["schema_errors"] == {}, q[v]
        cov = min(q[v]["streams_connected_fraction"].values())
        assert cov > 0.9, q[v]
        assert q[v]["flag"] == ("bad" if cov < 0.99 else "degraded" if cov < 0.999 else "good")
    assert (rec.root / "lake" / "_quality" / f"date={rec.day.isoformat()}.json").exists()
