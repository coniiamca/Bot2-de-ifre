"""Daily verification of the recording against ground truth (Phase 0 success criteria).

Two checks per UTC day; the result is ``lake/_checks/date=YYYY-MM-DD.json`` (status page):

* **trades** — every Binance aggregate trade in the official daily archive
  (data.binance.vision) must be in our capture with identical fields, inside the id window we
  were recording. A missing id is *explained* when the recorder reported it in-band (a
  ``trade_gap`` record) or when it falls in a moment the recorder was not capturing (restart,
  disk guard, market stream reconnect — a meta record lies within the missing stretch). Any
  other missing id is *unexplained* and fails the day, as do mismatched or extra trades.
* **book** — the order book rebuilt from the recorded diffs must equal every recorded REST
  snapshot (:func:`quanta.tools.book_audit.audit`).

The archive appears about a day later, so a day's trades stay ``waiting`` until it does; the
scheduler retries the last ``RETRY_DAYS`` days. Archives are deleted right after use (the disk
budget is small) and no download starts while free space is below the disk-guard floor.
Memory: one pass over the day's market segments keeps compact columns for every checked symbol
(≈ 55 bytes per trade, a few hundred MB for a 10-symbol universe).
"""

from __future__ import annotations

import bisect
import json
import os
import shutil
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa

from quanta.core.log import get_logger
from quanta.tools.book_audit import audit
from quanta.tools.raw_reader import days_around, iter_records, segment_files
from quanta.tools.verify_aggtrades import (
    ArchiveMissing,
    compare_tables,
    fetch_archive,
    read_archive_table,
    read_recorded_tables,
)

log = get_logger(__name__)

VENUE = "binance_usdm"
RETRY_DAYS = 3
DOWNLOAD_RESERVE_BYTES = 1e9  # an aggTrades zip is well below this
BREAK_TOLERANCE_MS = 5_000  # exchange time vs our arrival time around a break
PENDING = ("waiting", "error", "skipped")
# meta record type → why trades around it may be missing
BREAKS = {
    "recorder_start": "restart",
    "recorder_stop": "restart",
    "capture_start": "restart",
    "capture_stop": "restart",
    "disk_guard_on": "disk_guard",
    "disk_guard_off": "disk_guard",
    "write_backlog_dropped": "disk_guard",
}
MARKET_STREAM_PREFIX = f"{VENUE}:market"
Fetch = Callable[[str, date, Path], tuple[Path, str]]


def _free_bytes(path: Path) -> float:
    return float(shutil.disk_usage(path).free)


def checks_path(data_dir: Path, day: date) -> Path:
    return data_dir / "lake" / "_checks" / f"date={day.isoformat()}.json"


@dataclass(slots=True)
class CheckSettings:
    trade_symbols: list[str]
    book_symbols: list[str]
    cache_dir: Path | None = None  # default <data>/archive-cache
    min_free_bytes: float = 0.0
    retry_days: int = RETRY_DAYS


# -- recorder meta: in-band gaps and breaks ------------------------------------------------------
@dataclass(slots=True)
class MetaIndex:
    gaps: dict[str, list[tuple[int, int]]] = field(default_factory=dict)  # symbol → id ranges
    break_ms: list[int] = field(default_factory=list)  # sorted
    break_kind: list[str] = field(default_factory=list)


def load_meta(data_dir: Path, day: date) -> MetaIndex:
    """trade_gap ranges and capture breaks around ``day`` (a gap may be reported after
    midnight, a break just before)."""
    gaps: dict[str, list[tuple[int, int]]] = defaultdict(list)
    breaks: list[tuple[int, str]] = []
    for rec in iter_records(segment_files(data_dir, VENUE, "meta", days_around(day, 1, 1))):
        if rec.k != "meta":
            continue
        m = json.loads(bytes(rec.p))
        t = m.get("type")
        if t == "trade_gap" and m.get("symbol"):
            gaps[m["symbol"]].append((int(m["first_missing"]), int(m["last_missing"])))
        elif t in BREAKS:
            breaks.append((rec.t // 1_000_000, BREAKS[t]))
        elif (
            t == "ws_lifecycle"
            and str(m.get("stream", "")).startswith(MARKET_STREAM_PREFIX)
            and m.get("event") in ("connected", "disconnected", "connect_failed", "stopped")
        ):
            breaks.append((rec.t // 1_000_000, "reconnect"))
    breaks.sort()
    return MetaIndex(
        {s: sorted(r) for s, r in gaps.items()},
        [b[0] for b in breaks],
        [b[1] for b in breaks],
    )


def _in_ranges(i: int, ranges: list[tuple[int, int]], starts: list[int]) -> bool:
    k = bisect.bisect_right(starts, i) - 1
    return k >= 0 and ranges[k][0] <= i <= ranges[k][1]


def explain_missing(
    symbol: str, missing: pa.Table, meta: MetaIndex, tol_ms: int = BREAK_TOLERANCE_MS
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Split missing trades (id, time_ms; sorted by id) by explanation. Returns
    ({reason: count}, unexplained stretches)."""
    counts: dict[str, int] = {}
    unexplained: list[dict[str, Any]] = []
    ranges = meta.gaps.get(symbol, [])
    starts = [r[0] for r in ranges]
    ids = missing.column("id").to_pylist()
    times = missing.column("time_ms").to_pylist()
    rest: list[tuple[int, int]] = []
    for i, t in zip(ids, times, strict=True):
        if _in_ranges(i, ranges, starts):
            counts["trade_gap"] = counts.get("trade_gap", 0) + 1
        else:
            rest.append((i, t))
    # consecutive ids form one stretch; a break inside its time span explains it
    stretches: list[list[tuple[int, int]]] = []
    for i, t in rest:
        if stretches and i == stretches[-1][-1][0] + 1:
            stretches[-1].append((i, t))
        else:
            stretches.append([(i, t)])
    for st in stretches:
        t0 = min(t for _, t in st) - tol_ms
        t1 = max(t for _, t in st) + tol_ms
        k = bisect.bisect_left(meta.break_ms, t0)
        if k < len(meta.break_ms) and meta.break_ms[k] <= t1:
            reason = meta.break_kind[k]
            counts[reason] = counts.get(reason, 0) + len(st)
        else:
            counts["unexplained"] = counts.get("unexplained", 0) + len(st)
            if len(unexplained) < 20:
                unexplained.append(
                    {
                        "first_id": st[0][0],
                        "last_id": st[-1][0],
                        "count": len(st),
                        "from": _iso_ms(min(t for _, t in st)),
                        "to": _iso_ms(max(t for _, t in st)),
                    }
                )
    return counts, unexplained


def _iso_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"


# -- the two checks ---------------------------------------------------------------------------
def trade_result(
    symbol: str, day: date, archive: pa.Table, recorded: pa.Table, meta: MetaIndex
) -> dict[str, Any]:
    rep, missing = compare_tables(symbol, day, archive, recorded)
    out: dict[str, Any] = {
        "recorded": rep.recorded_count,
        "archive": rep.archive_count,
        "archive_in_window": rep.archive_in_window,
        "coverage": round(rep.archive_in_window / rep.archive_count, 6) if rep.archive_count else 0,
        "missing": rep.missing_in_window,
        "mismatched": rep.mismatched,
        "extra": rep.extra_ids,
    }
    if rep.recorded_count == 0:
        return out | {"status": "no_data"}
    reasons, stretches = explain_missing(symbol, missing, meta)
    unexplained = reasons.pop("unexplained", 0)
    out |= {"explained": reasons, "unexplained": unexplained, "unexplained_ranges": stretches}
    if rep.mismatch_examples:
        out["mismatch_examples"] = rep.mismatch_examples[:3]
    if unexplained or rep.mismatched or rep.extra_ids:
        out["status"] = "failed"
    else:
        out["status"] = "explained" if rep.missing_in_window else "ok"
    return out


def book_result(data_dir: Path, symbol: str, day: date) -> dict[str, Any]:
    rep = audit(data_dir, symbol, day)
    out: dict[str, Any] = {
        k: v for k, v in asdict(rep).items() if k not in ("symbol", "day", "mismatch_examples")
    }
    if rep.mismatch_examples:
        out["mismatch_examples"] = rep.mismatch_examples[:3]
    if rep.mismatched or rep.errors:
        out["status"] = "failed"
    else:
        out["status"] = "ok" if rep.compared else "no_data"
    return out


def day_status(doc: dict[str, Any]) -> str:
    states = [r["status"] for part in ("trades", "book") for r in doc.get(part, {}).values()]
    if "failed" in states:
        return "failed"
    if any(s in PENDING for s in states):
        return "waiting"
    if not states or all(s == "no_data" for s in states):
        return "no_data"
    return "ok"


def load_checks(data_dir: Path, day: date) -> dict[str, Any] | None:
    try:
        doc: dict[str, Any] = json.loads(checks_path(data_dir, day).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def _write(data_dir: Path, day: date, doc: dict[str, Any]) -> None:
    path = checks_path(data_dir, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, path)


def run_checks(
    data_dir: Path,
    day: date,
    settings: CheckSettings,
    *,
    disk_free: Callable[[Path], float] = _free_bytes,
    fetch: Fetch = fetch_archive,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run (or complete) the checks for ``day`` and write the result file. Final results of
    an earlier run are kept; only pending symbols are retried."""
    doc = load_checks(data_dir, day) or {}
    trades: dict[str, Any] = dict(doc.get("trades", {}))
    book: dict[str, Any] = dict(doc.get("book", {}))
    cache = settings.cache_dir or data_dir / "archive-cache"

    for sym in settings.book_symbols:
        if book.get(sym, {}).get("status", "") in ("", "error"):
            try:
                book[sym] = book_result(data_dir, sym, day)
            except Exception as exc:  # a corrupt segment must not stop the other checks
                log.exception("book_audit_failed", symbol=sym, date=day.isoformat())
                book[sym] = {"status": "error", "detail": repr(exc)}

    todo = [
        s for s in settings.trade_symbols if trades.get(s, {}).get("status", "") in ("", *PENDING)
    ]
    archives: dict[str, tuple[Path, str]] = {}
    for sym in todo:
        if disk_free(data_dir) < settings.min_free_bytes + DOWNLOAD_RESERVE_BYTES:
            trades[sym] = {"status": "skipped", "detail": "disk guard: not enough free space"}
            continue
        try:
            archives[sym] = fetch(sym, day, cache)
        except ArchiveMissing:
            trades[sym] = {"status": "waiting", "detail": "archive not published yet"}
        except OSError as exc:
            trades[sym] = {"status": "error", "detail": str(exc)}
    try:
        if archives:
            meta = load_meta(data_dir, day)
            recorded = read_recorded_tables(data_dir, list(archives), day)
            for sym, (path, sha) in archives.items():
                archive = read_archive_table(path)
                trades[sym] = trade_result(sym, day, archive, recorded.pop(sym), meta) | {
                    "archive_sha256": sha
                }
                del archive
                path.unlink(missing_ok=True)
    finally:
        for path, _ in archives.values():
            path.unlink(missing_ok=True)

    doc = {
        "date": day.isoformat(),
        "venue": VENUE,
        "checked_at": (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "trades": trades,
        "book": book,
    }
    doc["status"] = day_status(doc)
    _write(data_dir, day, doc)
    log.info("daily_checks_done", date=day.isoformat(), status=doc["status"])
    return doc


def _has_raw(data_dir: Path, day: date) -> bool:
    return any(
        (data_dir / "raw" / VENUE / ch / day.isoformat()).exists()
        for ch in ("market", "public_depth")
    )


def pending_days(data_dir: Path, today: date, settings: CheckSettings) -> list[date]:
    """Finished days of the retry window with raw data whose checks are missing or pending."""
    out: list[date] = []
    for back in range(settings.retry_days, 0, -1):
        day = today - timedelta(days=back)
        if not _has_raw(data_dir, day):
            continue
        doc = load_checks(data_dir, day)
        if (
            doc is None
            or doc.get("status") == "waiting"
            or set(settings.trade_symbols) - set(doc.get("trades", {}))
            or set(settings.book_symbols) - set(doc.get("book", {}))
        ):
            out.append(day)
    return out


def run_pending(
    data_dir: Path, settings: CheckSettings, today: date | None = None, **kw: Any
) -> list[dict[str, Any]]:
    days = pending_days(data_dir, today or datetime.now(UTC).date(), settings)
    return [run_checks(data_dir, d, settings, **kw) for d in days]
