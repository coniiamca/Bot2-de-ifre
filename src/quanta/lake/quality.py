"""Daily data quality report per venue (plan §8.4), computed from the normalized lake.

Dimensions:
* ``recording_span``: the part of the UTC day the recorder was running (from lifecycle).
* ``streams``: per stream, fraction of the recording span with a live connection.

Lifecycle state crosses midnight: a recorder that runs for days writes no start event on most
days. The state at 00:00 is rebuilt from the previous day's meta events (lake table, else raw
segments), tracking connections by id. Every connection starts or rotates within 24 h (it is
replaced after 23 h), so that one day is enough.
* ``integrity``: in-band gap/error events (depth gaps, trade gaps + missing ids, book errors,
  resets, subscription/RPC errors) and normalizer counters (invalid frames, schema errors —
  the latter signal exchange API drift).
* ``latency``: receive − exchange time percentiles for trades, per symbol.

Flag: ``bad`` (a stream below 99 % coverage or schema errors), ``degraded`` (any gap/error
event or a stream below 99.9 %), else ``good``. Research tooling reads these flags to warn
about or exclude partitions (plan §8.4).
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from quanta.lake.table import partition_path
from quanta.tools.raw_reader import iter_records, segment_files

EVENT_TYPES = (
    "depth_gap",
    "depth_reset",
    "depth_snapshot_stale",
    "trade_gap",
    "book_error",
    "book_reset",
    "subscribe_failed",
    "rpc_error",
    "instrument_changed",
    "universe_symbols_not_trading",
)


def _read(
    root: Path, venue: str, table: str, day: date, columns: list[str] | None = None
) -> pa.Table | None:
    p = partition_path(root, venue, table, day.isoformat())
    return pq.read_table(p, columns=columns) if p.exists() else None


# (ts_arrival ns, type, stream, detail)
Event = tuple[int, str, str | None, dict[str, Any]]
_STOPS = ("recorder_stop", "capture_stop")


def _table_events(meta: pa.Table) -> list[Event]:
    rows = zip(
        meta.column("ts_arrival").cast(pa.int64()).to_pylist(),
        meta.column("type").to_pylist(),
        meta.column("stream").to_pylist(),
        meta.column("detail_json").to_pylist(),
        strict=True,
    )
    events = [(t, ty, st, json.loads(d or "{}")) for t, ty, st, d in rows]
    return sorted(events, key=lambda e: e[0])  # stable: ties keep record order


def _day_events(root: Path, venue: str, day: date) -> list[Event]:
    """Meta events of a day from the lake, else straight from the raw segments."""
    meta = _read(root, venue, "meta_events", day)
    if meta is not None:
        return _table_events(meta)
    events: list[Event] = []
    for rec in iter_records(segment_files(root, venue, "meta", [day])):
        if rec.k == "meta":
            p = json.loads(bytes(rec.p))
            events.append((rec.t, p.get("type", ""), p.get("stream"), p))
    return sorted(events, key=lambda e: e[0])


@dataclass(slots=True)
class Lifecycle:
    """Recorder running state and the live connection ids of each stream."""

    running: bool = False
    conns: dict[str, set[str]] = field(default_factory=dict)

    def apply(self, ev: Event) -> None:
        _, typ, stream, detail = ev
        if typ == "recorder_start":
            self.running = True
            self.conns = {s: set() for s in self.conns}  # a new process: no connection yet
        elif typ in _STOPS:
            self.running = False
            self.conns = {s: set() for s in self.conns}
        elif typ == "ws_lifecycle" and stream:
            live = self.conns.setdefault(stream, set())
            event, conn = detail.get("event"), detail.get("conn_id")
            if event == "connected" and conn:
                live.add(conn)
            elif event == "disconnected":
                live.discard(conn)
            elif event == "stopped":
                live.clear()

    def copy(self) -> Lifecycle:
        return Lifecycle(self.running, {s: set(c) for s, c in self.conns.items()})


def end_state(events: list[Event]) -> Lifecycle:
    """State at the end of a day, replayed from an empty state."""
    lc = Lifecycle()
    for ev in events:
        lc.apply(ev)
    if not any(e[1] == "recorder_start" or e[1] in _STOPS for e in events):
        lc.running = any(lc.conns.values())  # running since before this day
    return lc


def stream_coverage(
    events: list[Event], day_start: int, day_end: int, init: Lifecycle | None = None
) -> tuple[dict[str, float], int]:
    """({stream: connected fraction of the running time}, running ns) within the day."""
    lc = init.copy() if init is not None else Lifecycle()
    running_ns = 0
    up: dict[str, int] = defaultdict(int)
    t = day_start

    def advance(now: int) -> None:
        nonlocal running_ns, t
        now = min(max(now, day_start), day_end)
        if lc.running:
            running_ns += now - t
        for s, live in lc.conns.items():
            if live:
                up[s] += now - t
        t = now

    for ev in events:
        advance(ev[0])
        lc.apply(ev)
    advance(day_end)
    if not running_ns:
        return {}, 0
    streams = sorted(set(lc.conns) | set(up))
    return {s: round(min(1.0, up[s] / running_ns), 6) for s in streams}, running_ns


def venue_quality(root: Path, venue: str, day: date) -> dict[str, Any] | None:
    mpath = root / "lake" / venue / f"_manifests/date={day.isoformat()}.json"
    if not mpath.exists():
        return None
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    day_start = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()) * 10**9
    day_end = day_start + 86_400 * 10**9
    day_events = _day_events(root, venue, day)
    init = end_state(_day_events(root, venue, day - timedelta(days=1)))
    coverage, span = stream_coverage(day_events, day_start, day_end, init)
    events: dict[str, int] = dict.fromkeys(EVENT_TYPES, 0)
    missing_trades = 0
    for _, t, _, d in day_events:
        if t in events:
            events[t] += 1
        if t == "trade_gap":
            missing_trades += int(d.get("count", 0))
    latency: dict[str, dict[str, float]] = {}
    trades = _read(root, venue, "trades", day, ["symbol", "ts_arrival", "ts_exchange"])
    if trades is not None and trades.num_rows:
        for sym in sorted(set(trades.column("symbol").to_pylist())):
            sub = trades.filter(pc.equal(trades.column("symbol"), sym))
            lat = pc.subtract(
                sub.column("ts_arrival").cast(pa.int64()),
                sub.column("ts_exchange").cast(pa.int64()),
            )
            q = pc.quantile(lat, q=[0.5, 0.99]).to_pylist()
            latency[sym] = {
                "trades": sub.num_rows,
                "p50_ms": round(q[0] / 1e6, 3),
                "p99_ms": round(q[1] / 1e6, 3),
            }
    schema_errors = sum(manifest.get("schema_errors", {}).values())
    min_cov = min(coverage.values(), default=0.0)
    gap_events = sum(
        events[k] for k in ("depth_gap", "trade_gap", "book_error", "subscribe_failed", "rpc_error")
    )
    if min_cov < 0.99 or schema_errors > 0:
        flag = "bad"
    elif gap_events > 0 or min_cov < 0.999 or manifest.get("invalid_frames", 0) > 0:
        flag = "degraded"
    else:
        flag = "good"
    return {
        "venue": venue,
        "date": day.isoformat(),
        "flag": flag,
        "recording_span_fraction": round(span / (day_end - day_start), 6),
        "streams_connected_fraction": coverage,
        "events": {k: v for k, v in events.items() if v},
        "missing_trade_ids": missing_trades,
        "invalid_frames": manifest.get("invalid_frames", 0),
        "schema_errors": manifest.get("schema_errors", {}),
        "duplicates_dropped": {
            t: x.get("duplicates_dropped", 0)
            for t, x in manifest["tables"].items()
            if x.get("duplicates_dropped")
        },
        "rows": {t: x["rows"] for t, x in manifest["tables"].items() if x["rows"]},
        "trade_latency": latency,
    }


def quality_report(root: Path, day: date, venues: list[str]) -> dict[str, Any]:
    out = {v: q for v in venues if (q := venue_quality(root, v, day)) is not None}
    path = root / "lake" / "_quality" / f"date={day.isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(out, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
    return out


def previous_utc_day(now: datetime | None = None) -> date:
    return ((now or datetime.now(UTC)) - timedelta(days=1)).date()
