"""Columnar table builder and deterministic partition writer.

Determinism contract (ADR-009): for identical raw inputs, normalizer version and library
versions, the Parquet bytes are identical. Achieved by:
* reading source segments in a fixed order and preserving record order,
* a *stable* sort on (symbol, ts_arrival) — ties keep arrival order,
* fixed writer options and no wall-clock values in file metadata.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Hashable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

NORMALIZER_VERSION = 1
TS = pa.timestamp("ns", tz="UTC")

COMMON_FIELDS = [
    pa.field("venue", pa.string(), nullable=False),
    pa.field("symbol", pa.string(), nullable=False),
    pa.field("ts_arrival", TS, nullable=False),
]
LINEAGE_FIELDS = [
    pa.field("conn_id", pa.string()),
    pa.field("conn_seq", pa.int64()),
]


def schema(*fields: pa.Field, lineage: bool = True) -> pa.Schema:
    return pa.schema([*COMMON_FIELDS, *fields, *(LINEAGE_FIELDS if lineage else [])])


class TableBuilder:
    """Accumulates rows column-wise; optional dedup by an exchange identity key."""

    def __init__(self, name: str, sch: pa.Schema) -> None:
        self.name = name
        self.schema = sch
        self.cols: dict[str, list[Any]] = {f.name: [] for f in sch}
        self._seen: set[Hashable] = set()
        self.duplicates = 0
        self.rows = 0

    def add(self, key: Hashable | None = None, /, **row: Any) -> bool:
        """Append a row. With ``key``, rows whose key was already seen are dropped
        (duplicates from overlapping connections — the earliest arrival wins)."""
        if key is not None:
            if key in self._seen:
                self.duplicates += 1
                return False
            self._seen.add(key)
        for name, col in self.cols.items():
            col.append(row.get(name))
        self.rows += 1
        return True

    def to_table(self, sort_by: Iterable[str] = ("symbol", "ts_arrival")) -> pa.Table:
        table = pa.Table.from_pydict(self.cols, schema=self.schema)
        if table.num_rows:
            idx = pc.sort_indices(table, sort_keys=[(k, "ascending") for k in sort_by])
            table = table.take(idx)
        return table


@dataclass(slots=True)
class PartitionResult:
    table: str
    path: str
    rows: int
    duplicates_dropped: int
    sha256: str


@dataclass(slots=True)
class DayManifest:
    venue: str
    date: str
    normalizer_version: int
    pyarrow_version: str
    sources: list[dict[str, Any]]
    invalid_frames: int = 0
    schema_errors: dict[str, int] = field(default_factory=dict)
    tables: dict[str, dict[str, Any]] = field(default_factory=dict)


def partition_path(root: Path, venue: str, table: str, date: str) -> Path:
    return root / "lake" / venue / table / f"date={date}" / "part-0.parquet"


def write_partition(
    root: Path, venue: str, date: str, builder: TableBuilder, sources_digest: str
) -> PartitionResult:
    table = builder.to_table()
    meta = {
        b"quanta.normalizer_version": str(NORMALIZER_VERSION).encode(),
        b"quanta.venue": venue.encode(),
        b"quanta.date": date.encode(),
        b"quanta.sources_sha256": sources_digest.encode(),
        b"quanta.duplicates_dropped": str(builder.duplicates).encode(),
    }
    table = table.replace_schema_metadata({**(table.schema.metadata or {}), **meta})
    path = partition_path(root, venue, builder.name, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    pq.write_table(
        table,
        tmp,
        compression="zstd",
        compression_level=3,
        row_group_size=1_000_000,
        write_statistics=True,
        use_dictionary=True,
    )
    digest = _sha256(tmp)
    os.replace(tmp, path)
    return PartitionResult(
        builder.name, str(path.relative_to(root)), table.num_rows, builder.duplicates, digest
    )


def write_day_manifest(root: Path, manifest: DayManifest) -> Path:
    path = root / "lake" / manifest.venue / f"_manifests/date={manifest.date}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(
            {
                "venue": manifest.venue,
                "date": manifest.date,
                "normalizer_version": manifest.normalizer_version,
                "pyarrow_version": manifest.pyarrow_version,
                "sources": manifest.sources,
                "invalid_frames": manifest.invalid_frames,
                "tables": manifest.tables,
            },
            indent=1,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def f64(x: Any) -> float | None:
    if x is None or x == "":
        return None
    return float(x)


def ms(x: Any) -> int | None:
    """Exchange millisecond timestamp → ns."""
    if x is None or x == "":
        return None
    return int(x) * 1_000_000
