"""File-based sources for the status page: quality reports, daily checks, data volume and
disk projection, access check, update state."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from quanta.tools.volume import volume

PHASE0_TARGET_DAYS = 3  # 72 hours of uninterrupted, verified recording
FULL_DAY_SPAN = 0.99


def load_quality(data_dir: Path, days: int = 14) -> list[dict[str, Any]]:
    qdir = data_dir / "lake" / "_quality"
    if not qdir.exists():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(qdir.glob("date=*.json"), reverse=True)[:days]:
        try:
            report = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        venues = {
            v: {
                "flag": q.get("flag"),
                "events": q.get("events", {}),
                "coverage_min": min(q.get("streams_connected_fraction", {}).values(), default=None),
                "missing_trade_ids": q.get("missing_trade_ids", 0),
                "schema_errors": sum(q.get("schema_errors", {}).values()),
                "span": q.get("recording_span_fraction"),
            }
            for v, q in report.items()
        }
        out.append({"date": p.stem.removeprefix("date="), "venues": venues})
    return out


# -- daily checks (quanta.lake.checks) ---------------------------------------------------------
REASONS = {
    "trade_gap": "bağlantı kopması",
    "reconnect": "bağlantı kopması",
    "restart": "yeniden başlatma",
    "disk_guard": "disk koruması",
}
FINAL_OK = ("ok", "explained")


def _worst(states: list[str]) -> str:
    for s in ("failed", "error", "skipped", "waiting"):
        if s in states:
            return "waiting" if s in ("error", "skipped") else s
    if any(s in FINAL_OK for s in states):
        return "ok"
    return "no_data"


def _trades_text(trades: dict[str, Any]) -> str:
    failed = [(s, r) for s, r in sorted(trades.items()) if r.get("status") == "failed"]
    if failed:
        parts = []
        for sym, r in failed[:3]:
            bits = []
            if r.get("unexplained"):
                bits.append(f"{r['unexplained']} açıklanamayan eksik")
            if r.get("mismatched"):
                bits.append(f"{r['mismatched']} farklı")
            if r.get("extra"):
                bits.append(f"{r['extra']} fazla")
            parts.append(f"{sym}: " + ", ".join(bits))
        return "; ".join(parts)
    waiting = [s for s, r in trades.items() if r.get("status") in ("waiting", "error", "skipped")]
    done = [r for r in trades.values() if r.get("status") in FINAL_OK]
    text = f"{len(done)} sembolde resmî arşivle aynı" if done else ""
    reasons: dict[str, int] = defaultdict(int)
    for r in done:
        for k, n in r.get("explained", {}).items():
            reasons[REASONS.get(k, k)] += n
    if reasons:
        why = ", ".join(f"{n} işlem {k}" for k, n in sorted(reasons.items()))
        text += f" (eksikler açıklandı: {why})"
    if waiting:
        skipped = any(trades[s].get("status") == "skipped" for s in waiting)
        note = "disk koruması nedeniyle indirilmedi" if skipped else "resmî arşiv bekleniyor"
        text += ("; " if text else "") + f"{len(waiting)} sembol: {note}"
    return text or "Bu gün için kayıt yok"


def _book_text(book: dict[str, Any]) -> str:
    failed = [(s, r) for s, r in sorted(book.items()) if r.get("status") in ("failed", "error")]
    if failed:
        return "; ".join(
            f"{s}: {r.get('mismatched', 0)} uyuşmazlık, {r.get('errors', 0)} hata"
            if r.get("status") == "failed"
            else f"{s}: denetlenemedi"
            for s, r in failed
        )
    ok = [s for s, r in sorted(book.items()) if r.get("status") == "ok"]
    if not ok:
        return "Karşılaştırılacak anlık görüntü yok"
    n = sum(book[s].get("compared", 0) for s in ok)
    return f"{', '.join(ok)}: {n} anlık görüntüyle birebir aynı"


def load_checks(data_dir: Path, days: int = 14) -> list[dict[str, Any]]:
    cdir = data_dir / "lake" / "_checks"
    if not cdir.exists():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(cdir.glob("date=*.json"), reverse=True)[:days]:
        doc = _load_json(p)
        if doc is None:
            continue
        trades: dict[str, Any] = doc.get("trades", {})
        book: dict[str, Any] = doc.get("book", {})
        out.append(
            {
                "date": doc.get("date", p.stem.removeprefix("date=")),
                "status": doc.get("status"),
                "checked_at": doc.get("checked_at"),
                "trades_state": _worst([r.get("status", "") for r in trades.values()]),
                "trades_text": _trades_text(trades),
                "book_state": _worst([r.get("status", "") for r in book.values()]),
                "book_text": _book_text(book),
                "unexplained": sum(int(r.get("unexplained", 0)) for r in trades.values()),
            }
        )
    return out


def phase0_progress(checks: list[dict[str, Any]], quality: list[dict[str, Any]]) -> dict[str, Any]:
    """Consecutive full, verified days up to the latest finished check (the Phase 0 goal is
    72 hours: three full UTC days with no unexplained gap and trades equal to the archive)."""
    span = {q["date"]: (q.get("venues", {}).get("binance_usdm") or {}).get("span") for q in quality}
    days = 0
    prev: date | None = None
    for c in checks:  # newest first
        if c.get("status") == "waiting" and days == 0 and prev is None:
            continue  # the archive of the latest day is not out yet
        d = date.fromisoformat(c["date"])
        full = (span.get(c["date"]) or 0) >= FULL_DAY_SPAN
        if c.get("status") != "ok" or not full or (prev and d != prev - timedelta(days=1)):
            break
        days, prev = days + 1, d
    return {"full_days": days, "target_days": PHASE0_TARGET_DAYS}


# -- volume and disk projection -------------------------------------------------------------
def lake_volume(data_dir: Path) -> dict[str, float]:
    """MB of normalized Parquet per day (recorder venues: those with raw data)."""
    out: dict[str, float] = defaultdict(float)
    raw = data_dir / "raw"
    venues = [p.name for p in raw.iterdir() if p.is_dir()] if raw.exists() else []
    for v in venues:
        for p in (data_dir / "lake" / v).glob("*/date=*/*.parquet"):
            try:
                out[p.parent.name.removeprefix("date=")] += p.stat().st_size / 1e6
            except OSError:
                continue
    return dict(out)


def disk_projection(
    rows: list[dict[str, Any]],
    today: date,
    day_fraction: float,
    free_bytes: float | None,
    floor_bytes: float | None,
) -> dict[str, Any] | None:
    """Average daily growth (raw segments + lake) and the days until the disk-guard floor.

    Uses the finished days, skipping the oldest (usually a partial first day); with a single
    finished day, today's raw data so far is extrapolated (``estimated``)."""
    if free_bytes is None:
        return None
    raw = {r["date"]: sum(v["compressed_mb"] for v in r["venues"].values()) for r in rows}
    lake = {r["date"]: r.get("lake_mb", 0.0) for r in rows}
    with_lake = [d for d in raw if lake.get(d, 0) > 0 and raw[d] > 0]
    lake_ratio = (
        sum(lake[d] for d in with_lake) / sum(raw[d] for d in with_lake) if with_lake else 0.0
    )
    past = sorted(d for d in raw if d < today.isoformat())
    estimated = False
    if len(past) >= 2:
        basis = past[1:][-7:]
        mb = sum(raw[d] for d in basis) / len(basis)
    elif len(past) == 1 and day_fraction >= 0.125 and raw.get(today.isoformat()):
        basis, estimated = [today.isoformat()], True
        mb = raw[today.isoformat()] / day_fraction
    else:
        return None
    gb_per_day = mb * (1 + lake_ratio) / 1000
    if gb_per_day <= 0:
        return None
    headroom = max(0.0, free_bytes - (floor_bytes or 0.0))
    return {
        "gb_per_day": round(gb_per_day, 2),
        "days_left": round(headroom / 1e9 / gb_per_day, 1),
        "estimated": estimated,
        "basis_days": len(basis),
        "lake_ratio": round(lake_ratio, 2),
    }


def load_access(path: Path) -> dict[str, Any] | None:
    return _load_json(path)


def load_update(data_dir: Path) -> dict[str, Any] | None:
    """State written by deploy/auto-update.sh (``<data>/update.json``), if enabled."""
    return _load_json(data_dir / "update.json")


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


class VolumeCache:
    """``volume()`` scans every manifest; recompute at most every ``ttl_s``."""

    def __init__(self, data_dir: Path, ttl_s: float = 600) -> None:
        self.data_dir = data_dir
        self.ttl_s = ttl_s
        self._at = 0.0
        self._value: list[dict[str, Any]] = []

    def get(self, days: int = 14) -> list[dict[str, Any]]:
        if time.monotonic() - self._at > self.ttl_s or not self._at:
            raw = volume(self.data_dir)
            lake = lake_volume(self.data_dir)
            rows = []
            for day in sorted(raw, reverse=True)[:days]:
                per: dict[str, dict[str, float]] = {}
                for key, v in raw[day].items():
                    venue = key.split("/", 1)[0]
                    agg = per.setdefault(venue, {"compressed_mb": 0.0, "records": 0.0})
                    agg["compressed_mb"] += v["compressed_mb"]
                    agg["records"] += v["records"]
                rows.append({"date": day, "venues": per, "lake_mb": round(lake.get(day, 0.0), 1)})
            self._value = rows
            self._at = time.monotonic()
        return self._value


def load_trader(path: Path | None, now: float) -> dict[str, Any] | None:
    """The demo trader's state.json (None when no trader is installed), with its age."""
    if path is None:
        return None
    doc = _load_json(path)
    if doc is None:
        return None
    ts_ns = doc.get("ts_ns") or 0
    doc["age_s"] = round(now - ts_ns / 1e9, 1) if ts_ns else None
    doc["journal"] = (doc.get("journal") or [])[:8]
    doc["canary"] = (doc.get("canary") or [])[-8:]
    doc["errors"] = (doc.get("errors") or [])[-5:]
    return doc
