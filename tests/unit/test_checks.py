"""Daily checks: explanation of missing trades, day status, retry window, depth ordering."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pyarrow as pa

from quanta.lake.checks import (
    CheckSettings,
    MetaIndex,
    checks_path,
    day_status,
    explain_missing,
    pending_days,
)
from quanta.tools.book_audit import in_order
from quanta.venues.binance_usdm.messages import DepthUpdate


def _missing(rows: list[tuple[int, int]]) -> pa.Table:
    return pa.table(
        {"id": pa.array([r[0] for r in rows], pa.int64()), "time_ms": [r[1] for r in rows]}
    )


def test_missing_trades_are_explained_by_gap_records_and_breaks() -> None:
    meta = MetaIndex(
        gaps={"BTCUSDT": [(10, 12)]},
        break_ms=[1_000_000, 2_000_000],
        break_kind=["restart", "disk_guard"],
    )
    missing = _missing(
        [
            (10, 500), (11, 501), (12, 502),  # reported in-band (trade_gap)
            (20, 999_000), (21, 1_001_000),  # a restart at t=1_000_000 lies in this stretch
            (30, 2_004_000),  # within the tolerance after the disk guard switched
            (40, 3_000_000), (41, 3_000_100),  # nothing explains this
        ]
    )  # fmt: skip
    counts, stretches = explain_missing("BTCUSDT", missing, meta)
    assert counts == {"trade_gap": 3, "restart": 2, "disk_guard": 1, "unexplained": 2}
    assert stretches == [
        {
            "first_id": 40,
            "last_id": 41,
            "count": 2,
            "from": "1970-01-01T00:50:00.000Z",
            "to": "1970-01-01T00:50:00.100Z",
        }
    ]
    # gap records of another symbol explain nothing here
    counts, _ = explain_missing("ETHUSDT", _missing([(10, 500)]), meta)
    assert counts == {"unexplained": 1}


def test_day_status() -> None:
    def doc(*states: str) -> dict[str, object]:
        return {"trades": {f"S{i}": {"status": s} for i, s in enumerate(states)}, "book": {}}

    assert day_status(doc("ok", "explained")) == "ok"
    assert day_status(doc("ok", "waiting")) == "waiting"
    assert day_status(doc("waiting", "failed")) == "failed"
    assert day_status(doc("no_data")) == "no_data"
    assert day_status(doc()) == "no_data"


def test_pending_days_retry_window(tmp_path: Path) -> None:
    s = CheckSettings(["BTCUSDT"], ["BTCUSDT"], retry_days=3)
    today = date(2026, 9, 28)
    for d in ("2026-09-24", "2026-09-25", "2026-09-26", "2026-09-27"):
        (tmp_path / "raw" / "binance_usdm" / "market" / d).mkdir(parents=True)

    def write(day: date, status: str, trades: tuple[str, ...] = ("BTCUSDT",)) -> None:
        p = checks_path(tmp_path, day)
        p.parent.mkdir(parents=True, exist_ok=True)
        doc = {"status": status, "trades": {t: {} for t in trades}, "book": {"BTCUSDT": {}}}
        p.write_text(json.dumps(doc))

    write(date(2026, 9, 25), "ok")
    write(date(2026, 9, 26), "waiting")
    write(date(2026, 9, 27), "failed", trades=())  # a symbol added to the config since
    # 24th: outside the window; 25th: final; 26th: waiting; 27th: new symbol
    assert pending_days(tmp_path, today, s) == [date(2026, 9, 26), date(2026, 9, 27)]


def _ev(u: int) -> DepthUpdate:
    return DepthUpdate(E=0, T=0, s="X", U=u, u=u, pu=u - 1, b=[], a=[])


def test_depth_events_are_reordered_and_deduplicated() -> None:
    # two overlapping connections (rotation): interleaved, partly duplicated, one late
    arrivals = [1, 2, 4, 3, 3, 5, 4, 6, 8, 7]
    assert [e.u for e in in_order(map(_ev, arrivals), window=3)] == [1, 2, 3, 4, 5, 6, 7, 8]
    # beyond the window an event is too late to reorder: it is dropped, never emitted twice
    assert [e.u for e in in_order(map(_ev, [5, 6, 7, 1]), window=1)] == [5, 6, 7]
