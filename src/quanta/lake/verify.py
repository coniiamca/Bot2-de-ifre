"""Reproducibility check for the lake: raw → Parquet must be bit-for-bit repeatable."""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from quanta.lake.normalize import VENUES, normalize_day


def verify_day(root: Path, day: date) -> dict[str, Any]:
    report: dict[str, Any] = {"date": day.isoformat(), "venues": {}, "ok": True}
    for venue in VENUES:
        manifest_path = root / "lake" / venue / f"_manifests/date={day.isoformat()}.json"
        if not manifest_path.exists():
            continue
        stored = json.loads(manifest_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            troot = Path(tmp)
            # link raw segments into the temp root (read-only use)
            src = root / "raw" / venue
            shutil.copytree(src, troot / "raw" / venue, copy_function=_link_or_copy)
            fresh = normalize_day(troot, venue, day)
        diffs = {
            t: (stored["tables"].get(t, {}).get("sha256"), x.get("sha256"))
            for t, x in fresh.tables.items()
            if stored["tables"].get(t, {}).get("sha256") != x.get("sha256")
        }
        report["venues"][venue] = {"tables": len(fresh.tables), "mismatches": diffs}
        if diffs:
            report["ok"] = False
    return report


def _link_or_copy(src: str, dst: str) -> None:
    try:
        Path(dst).hardlink_to(src)
    except OSError:
        shutil.copy2(src, dst)
