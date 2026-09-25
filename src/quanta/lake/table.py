"""Columnar table builder and deterministic partition writer.

Determinism contract (ADR-009): for identical raw inputs, normalizer version and library
versions, the Parquet bytes are identical. Achieved by:
* reading source segments in a fixed order and preserving record order,
* a *stable* sort on (symbol, ts_arrival) — ties keep arrival order,
* fixed writer options and no wall-clock values in file metadata.

Memory is bounded: a full day of L2 order book updates is ~10⁸ rows, far more than fits in
RAM on a small (shared) server. With a spill directory, a builder sorts every
``SPILL_ROWS`` rows and writes them to a compressed Arrow file, one record batch per
(symbol, 10-minute bucket of ts_arrival). The partition writer then merges bucket by bucket:
the chunk slices of a bucket, in chunk order, stably sorted by ts_arrival. That is exactly the
global stable sort, so the Parquet bytes equal the in-memory path (tested).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import deque
from collections.abc import Callable, Hashable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

NORMALIZER_VERSION = 1
TS = pa.timestamp("ns", tz="UTC")
SPILL_ROWS = 250_000
BUCKET_NS = 600 * 10**9
ROW_GROUP_ROWS = 1_000_000
SORT_KEYS = ("symbol", "ts_arrival")

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


class SeenKeys:
    """Duplicate filter for exchange identity keys (duplicates come from overlapping
    connections and arrive within seconds). Exact up to ``exact_max`` keys; beyond that,
    keys older than ``window_ns`` of arrival time are forgotten so memory stays bounded on
    busy streams. Deterministic: depends only on the input sequence."""

    def __init__(self, exact_max: int = 1_000_000, window_ns: int = 3600 * 10**9) -> None:
        self.exact_max = exact_max
        self.window_ns = window_ns
        self._set: set[Hashable] = set()
        self._order: deque[tuple[int, Hashable]] = deque()

    def add(self, key: Hashable, ts_ns: int) -> bool:
        """True if ``key`` is new (and remember it), False for a duplicate."""
        if key in self._set:
            return False
        self._set.add(key)
        self._order.append((ts_ns, key))
        if len(self._set) > self.exact_max:
            cutoff = ts_ns - self.window_ns
            while self._order and self._order[0][0] < cutoff:
                self._set.discard(self._order.popleft()[1])
        return True

    def __len__(self) -> int:
        return len(self._set)


class DiskGuardError(OSError):
    """Free space fell below the disk-guard floor while writing lake data."""


@dataclass(slots=True)
class _Chunk:
    path: Path
    batches: dict[tuple[str, int], int]  # (symbol, bucket) → record batch index


def _sorted(table: pa.Table) -> pa.Table:
    if table.num_rows < 2:
        return table
    return table.take(pc.sort_indices(table, sort_keys=[(k, "ascending") for k in SORT_KEYS]))


def _read_batch(path: Path, index: int) -> pa.RecordBatch:
    with pa.OSFile(str(path), "rb") as f:
        return pa.ipc.open_file(f).get_batch(index)


class TableBuilder:
    """Accumulates rows column-wise; optional dedup by an exchange identity key; spills to
    disk when ``spill_dir`` is set (see module docstring)."""

    def __init__(self, name: str, sch: pa.Schema) -> None:
        self.name = name
        self.schema = sch
        self.cols: dict[str, list[Any]] = {f.name: [] for f in sch}
        self._seen = SeenKeys()
        self.duplicates = 0
        self.rows = 0
        self.spill_dir: Path | None = None
        self.spill_rows = SPILL_ROWS
        self.before_spill: Callable[[], None] | None = None  # e.g. a disk-space check
        self._pending = 0
        self._chunks: list[_Chunk] = []

    def add(self, key: Hashable | None = None, /, **row: Any) -> bool:
        """Append a row. With ``key``, rows whose key was already seen are dropped
        (duplicates from overlapping connections — the earliest arrival wins)."""
        if key is not None and not self._seen.add(key, row.get("ts_arrival") or 0):
            self.duplicates += 1
            return False
        for name, col in self.cols.items():
            col.append(row.get(name))
        self.rows += 1
        self._pending += 1
        if self.spill_dir is not None and self._pending >= self.spill_rows:
            self._spill()
        return True

    def _take_pending(self) -> pa.Table:
        table = pa.Table.from_pydict(self.cols, schema=self.schema)
        self.cols = {f.name: [] for f in self.schema}
        self._pending = 0
        return table

    def to_table(self) -> pa.Table:
        """All rows sorted by (symbol, ts_arrival) in memory (no spilled chunks)."""
        assert not self._chunks, "spilled builder: use sorted_parts()"
        return _sorted(pa.Table.from_pydict(self.cols, schema=self.schema))

    def _spill(self) -> None:
        assert self.spill_dir is not None
        if self.before_spill is not None:
            self.before_spill()
        table = _sorted(self._take_pending())
        syms = table.column("symbol").to_pylist()
        buckets = pc.divide(table.column("ts_arrival").cast(pa.int64()), BUCKET_NS).to_pylist()
        self.spill_dir.mkdir(parents=True, exist_ok=True)
        path = self.spill_dir / f"{self.name}-{len(self._chunks):05d}.arrow"
        index: dict[tuple[str, int], int] = {}
        opts = pa.ipc.IpcWriteOptions(compression="zstd")
        with (
            pa.OSFile(str(path), "wb") as sink,
            pa.ipc.new_file(sink, self.schema, options=opts) as w,
        ):
            start = 0
            for i in range(1, len(syms) + 1):
                if i == len(syms) or (syms[i], buckets[i]) != (syms[start], buckets[start]):
                    index[(syms[start], buckets[start])] = len(index)
                    w.write_table(table.slice(start, i - start), max_chunksize=i - start)
                    start = i
        self._chunks.append(_Chunk(path, index))

    def sorted_parts(self) -> Iterator[pa.Table]:
        """All rows sorted by (symbol, ts_arrival), stably, as consecutive tables."""
        if not self._chunks:
            yield self.to_table()
            return
        if self._pending:
            self._spill()
        for key in sorted({k for c in self._chunks for k in c.batches}):
            # open per read: no file descriptor or mapping is held across a day of chunks
            parts = [_read_batch(c.path, c.batches[key]) for c in self._chunks if key in c.batches]
            table = pa.Table.from_batches(parts, schema=self.schema)
            if len(parts) > 1:
                table = table.take(pc.sort_indices(table, sort_keys=[("ts_arrival", "ascending")]))
            yield table


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
    meta = {
        b"quanta.normalizer_version": str(NORMALIZER_VERSION).encode(),
        b"quanta.venue": venue.encode(),
        b"quanta.date": date.encode(),
        b"quanta.sources_sha256": sources_digest.encode(),
        b"quanta.duplicates_dropped": str(builder.duplicates).encode(),
    }
    sch = builder.schema.with_metadata({**(builder.schema.metadata or {}), **meta})
    path = partition_path(root, venue, builder.name, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    rows = 0
    # Row groups of exactly ROW_GROUP_ROWS rows, as one pq.write_table call would produce.
    with pq.ParquetWriter(
        tmp,
        sch,
        compression="zstd",
        compression_level=3,
        write_statistics=True,
        use_dictionary=True,
    ) as writer:
        buf: list[pa.Table] = []
        buffered = 0
        for part in builder.sorted_parts():
            buf.append(part.replace_schema_metadata(sch.metadata))
            buffered += part.num_rows
            while buffered >= ROW_GROUP_ROWS:
                whole = pa.concat_tables(buf)
                writer.write_table(whole.slice(0, ROW_GROUP_ROWS), row_group_size=ROW_GROUP_ROWS)
                rest = whole.slice(ROW_GROUP_ROWS)
                buf, buffered = [rest], rest.num_rows
                rows += ROW_GROUP_ROWS
        if buffered or not rows:
            tail = pa.concat_tables(buf) if buf else sch.empty_table()
            writer.write_table(tail, row_group_size=ROW_GROUP_ROWS)
            rows += buffered
    digest = _sha256(tmp)
    os.replace(tmp, path)
    return PartitionResult(
        builder.name, str(path.relative_to(root)), rows, builder.duplicates, digest
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
