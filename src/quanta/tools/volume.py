"""Data volume report from segment manifests (plan §8.6: measure GB/day per stream)."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from quanta.recorder.segment import SEGMENT_SUFFIX, manifest_path, read_manifest


def volume(data_dir: Path) -> dict[str, dict[str, dict[str, float]]]:
    """{day: {venue/channel: {records, compressed_mb, raw_mb, ratio}}}"""
    acc: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    raw = data_dir / "raw"
    for seg in raw.rglob(f"*{SEGMENT_SUFFIX}") if raw.exists() else []:
        if not manifest_path(seg).exists():
            continue
        info = read_manifest(seg)
        key = f"{info.venue}/{info.channel}"
        day = info.period_start[:10]
        a = acc[day][key]
        a[0] += info.records
        a[1] += info.bytes
        a[2] += info.raw_bytes
    out: dict[str, dict[str, dict[str, float]]] = {}
    for day, chans in sorted(acc.items()):
        out[day] = {
            k: {
                "records": v[0],
                "compressed_mb": round(v[1] / 1e6, 3),
                "raw_mb": round(v[2] / 1e6, 3),
                "ratio": round(v[2] / v[1], 2) if v[1] else 0.0,
            }
            for k, v in sorted(chans.items())
        }
    return out
