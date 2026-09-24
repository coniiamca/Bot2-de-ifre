"""Daily data quality report per venue (plan §8.4), computed from the normalized lake.

Dimensions:
* ``recording_span``: the part of the UTC day the recorder was running (from lifecycle).
* ``streams``: per stream, fraction of the recording span with a live connection.
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
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from quanta.lake.table import partition_path

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


def _read(root: Path, venue: str, table: str, day: date) -> pa.Table | None:
    p = partition_path(root, venue, table, day.isoformat())
    return pq.read_table(p) if p.exists() else None


def _stream_coverage(meta: pa.Table, day_start: int, day_end: int) -> tuple[dict[str, Any], int]:
    rows = meta.select(["ts_arrival", "type", "stream", "detail_json"])
    ts = rows.column("ts_arrival").cast(pa.int64()).to_pylist()
    types = rows.column("type").to_pylist()
    streams = rows.column("stream").to_pylist()
    details = rows.column("detail_json").to_pylist()
    order = sorted(range(len(ts)), key=lambda i: ts[i])
    starts = [ts[i] for i in order if types[i] == "recorder_start"]
    stops = [ts[i] for i in order if types[i] in ("recorder_stop", "capture_stop")]
    span_start = max(day_start, min(starts) if starts else (ts[order[0]] if ts else day_start))
    span_end = min(day_end, max(stops) if stops else (ts[order[-1]] if ts else day_start))
    live: dict[str, int] = {}
    since: dict[str, int] = {}
    up_ns: dict[str, int] = {}
    for i in order:
        if types[i] != "ws_lifecycle" or not streams[i]:
            continue
        s, t = streams[i], min(max(ts[i], span_start), span_end)
        event = json.loads(details[i] or "{}").get("event")
        live.setdefault(s, 0)
        up_ns.setdefault(s, 0)
        was_up = live[s] > 0
        if event == "connected":
            live[s] += 1
        elif event == "disconnected":
            live[s] = max(0, live[s] - 1)
        elif event == "stopped":
            live[s] = 0
        else:
            continue
        if was_up and live[s] == 0:
            up_ns[s] += t - since[s]
        elif not was_up and live[s] > 0:
            since[s] = t
    for s, n in live.items():
        if n > 0:
            up_ns[s] += span_end - since[s]
    span = max(1, span_end - span_start)
    cov = {s: round(v / span, 6) for s, v in sorted(up_ns.items())}
    return cov, span


def venue_quality(root: Path, venue: str, day: date) -> dict[str, Any] | None:
    mpath = root / "lake" / venue / f"_manifests/date={day.isoformat()}.json"
    if not mpath.exists():
        return None
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    day_start = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()) * 10**9
    day_end = day_start + 86_400 * 10**9
    meta = _read(root, venue, "meta_events", day)
    coverage: dict[str, Any] = {}
    span = 0
    events: dict[str, int] = dict.fromkeys(EVENT_TYPES, 0)
    missing_trades = 0
    if meta is not None and meta.num_rows:
        coverage, span = _stream_coverage(meta, day_start, day_end)
        for t, d in zip(
            meta.column("type").to_pylist(), meta.column("detail_json").to_pylist(), strict=True
        ):
            if t in events:
                events[t] += 1
            if t == "trade_gap":
                missing_trades += int(json.loads(d or "{}").get("count", 0))
    latency: dict[str, dict[str, float]] = {}
    trades = _read(root, venue, "trades", day)
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
