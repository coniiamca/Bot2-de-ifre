"""File-based sources for the status page: quality reports, data volume, access check."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from quanta.tools.volume import volume


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
            }
            for v, q in report.items()
        }
        out.append({"date": p.stem.removeprefix("date="), "venues": venues})
    return out


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
            rows = []
            for day in sorted(raw, reverse=True)[:days]:
                per: dict[str, dict[str, float]] = {}
                for key, v in raw[day].items():
                    venue = key.split("/", 1)[0]
                    agg = per.setdefault(venue, {"compressed_mb": 0.0, "records": 0.0})
                    agg["compressed_mb"] += v["compressed_mb"]
                    agg["records"] += v["records"]
                rows.append({"date": day, "venues": per})
            self._value = rows
            self._at = time.monotonic()
        return self._value
