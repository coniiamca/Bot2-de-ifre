"""Normalize one UTC day of raw capture into the lake (all tables of one venue).

Records are assigned to a day by **arrival** time (the hour of the raw segment), consistent
with arrival-time semantics (ADR-004). Only finalized segments are read; a day that still
has ``.partial`` segments is refused unless ``allow_partial`` is set.

Records are streamed and the table builders spill to ``lake/<venue>/.spill-<date>/`` (removed
afterwards), so memory stays bounded for a full day of L2 data. Spilling stops with
:class:`DiskGuardError` when free space falls below ``min_free_bytes``.
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Protocol

import pyarrow as pa

from quanta.lake import binance_usdm, bybit_linear, deribit
from quanta.lake.table import (
    NORMALIZER_VERSION,
    DayManifest,
    DiskGuardError,
    TableBuilder,
    write_day_manifest,
    write_partition,
)
from quanta.recorder.segment import PARTIAL_SUFFIX, SEGMENT_SUFFIX, read_manifest
from quanta.tools.raw_reader import RawRecord, iter_records


class Normalizer(Protocol):
    t: dict[str, TableBuilder]
    invalid_frames: int
    schema_errors: dict[str, int]

    def feed(self, channel: str, records: object) -> None: ...


VENUES: dict[str, tuple[type, tuple[str, ...]]] = {
    "binance_usdm": (
        binance_usdm.BinanceUsdmNormalizer,
        ("rest", "meta", "public_depth", "public_bbo", "market"),
    ),
    "bybit_linear": (bybit_linear.BybitLinearNormalizer, ("rest", "meta", "public_book", "market")),
    "deribit": (deribit.DeribitNormalizer, ("rest", "meta", "public_book", "market")),
}


class PartialDataError(RuntimeError):
    pass


def _day_files(root: Path, venue: str, channel: str, day: date) -> tuple[list[Path], list[Path]]:
    d = root / "raw" / venue / channel / day.isoformat()
    if not d.exists():
        return [], []
    final = sorted(
        (p for p in d.iterdir() if p.name.endswith(SEGMENT_SUFFIX)),
        key=lambda p: (p.name.split(".")[2], int(p.name.split(".")[3])),
    )
    partial = [p for p in d.iterdir() if p.name.endswith(SEGMENT_SUFFIX + PARTIAL_SUFFIX)]
    return final, partial


def _free_bytes(path: Path) -> float:
    return float(shutil.disk_usage(path).free)


def normalize_day(
    root: Path,
    venue: str,
    day: date,
    *,
    allow_partial: bool = False,
    min_free_bytes: float = 0.0,
    disk_free: Callable[[Path], float] = _free_bytes,
) -> DayManifest:
    spill = root / "lake" / venue / f".spill-{day.isoformat()}"
    shutil.rmtree(spill, ignore_errors=True)  # left over by an interrupted run
    try:
        return _normalize(root, venue, day, spill, allow_partial, min_free_bytes, disk_free)
    finally:
        shutil.rmtree(spill, ignore_errors=True)


def _normalize(
    root: Path,
    venue: str,
    day: date,
    spill: Path,
    allow_partial: bool,
    min_free_bytes: float,
    disk_free: Callable[[Path], float],
) -> DayManifest:
    cls, channels = VENUES[venue]
    norm: Normalizer = cls()

    def check_space() -> None:
        free = disk_free(root)
        if free < min_free_bytes:
            raise DiskGuardError(
                f"{free / 1e9:.1f} GB free < {min_free_bytes / 1e9:.1f} GB (disk guard)"
            )

    for builder in norm.t.values():
        builder.spill_dir = spill / builder.name
        builder.before_spill = check_space
    sources: list[dict[str, object]] = []
    for ch in channels:
        files, partial = _day_files(root, venue, ch, day)
        if partial and not allow_partial:
            raise PartialDataError(f"{venue}/{ch}/{day}: unfinalized segments {partial}")
        for f in files:
            info = read_manifest(f)
            sources.append(
                {"file": f.name, "channel": ch, "sha256": info.sha256, "records": info.records}
            )
        norm.feed(ch, _records(files))
    check_space()
    digest = hashlib.sha256(
        "\n".join(f"{s['file']}:{s['sha256']}" for s in sources).encode()
    ).hexdigest()
    manifest = DayManifest(
        venue=venue,
        date=day.isoformat(),
        normalizer_version=NORMALIZER_VERSION,
        pyarrow_version=pa.__version__,
        sources=sources,
        invalid_frames=norm.invalid_frames,
    )
    for name, builder in norm.t.items():
        if builder.rows == 0:
            manifest.tables[name] = {"rows": 0}
            continue
        res = write_partition(root, venue, day.isoformat(), builder, digest)
        manifest.tables[name] = {
            "rows": res.rows,
            "path": res.path,
            "sha256": res.sha256,
            "duplicates_dropped": res.duplicates_dropped,
        }
    write_day_manifest(root, manifest)
    return manifest


def _records(files: list[Path]) -> Iterator[RawRecord]:
    return iter_records(files)
