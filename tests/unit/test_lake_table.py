"""Table builder: spilling to disk must not change a single byte of the output."""

from __future__ import annotations

import hashlib
import random
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanta.lake import table as lt
from quanta.lake.table import SeenKeys, TableBuilder, schema, write_partition

SCHEMA = schema(pa.field("price", pa.float64()), pa.field("side", pa.string()))
BASE = 1_790_000_000 * 10**9


def _rows(seed: int = 3) -> list[dict[str, object]]:
    rng = random.Random(seed)
    rows: list[dict[str, object]] = []
    # "REST" rows first, spread over the day, then "WS" rows in arrival order: the input is
    # not globally sorted, and many rows share a timestamp (ties must keep input order)
    for i in range(300):
        ts = BASE + rng.randrange(0, 86_400) * 10**9
        rows.append({"symbol": rng.choice("ABC"), "ts_arrival": ts, "price": float(i)})
    ts = BASE
    for i in range(3000):
        ts += rng.choice([0, 0, 10**9, 7 * 10**9, 400 * 10**9])
        rows.append({"symbol": rng.choice("ABC"), "ts_arrival": ts, "price": 1000.0 + i})
    for i, r in enumerate(rows):
        r.update(venue="v", side=("bid", "ask")[i % 2], conn_id=f"c{i % 3}", conn_seq=i)
    return rows


def _write(root: Path, spill: Path | None) -> str:
    b = TableBuilder("t", SCHEMA)
    if spill is not None:
        b.spill_dir, b.spill_rows = spill, 37
    for r in _rows():
        b.add(None, **r)
    res = write_partition(root, "v", "2026-09-26", b, "digest")
    assert res.rows == 3300
    return res.sha256


def test_spilled_output_is_byte_identical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lt, "ROW_GROUP_ROWS", 500)  # several row groups
    monkeypatch.setattr(lt, "BUCKET_NS", 3600 * 10**9)
    in_memory = _write(tmp_path / "a", None)
    spilled = _write(tmp_path / "b", tmp_path / "spill")
    assert spilled == in_memory
    assert len(list((tmp_path / "spill").glob("*.arrow"))) > 50

    # ... and to the previous implementation: one pq.write_table of the stably sorted table
    b = TableBuilder("t", SCHEMA)
    for r in _rows():
        b.add(None, **r)
    meta = {
        b"quanta.normalizer_version": b"1",
        b"quanta.venue": b"v",
        b"quanta.date": b"2026-09-26",
        b"quanta.sources_sha256": b"digest",
        b"quanta.duplicates_dropped": b"0",
    }
    pq.write_table(
        b.to_table().replace_schema_metadata(meta),
        tmp_path / "ref.parquet",
        compression="zstd",
        compression_level=3,
        row_group_size=500,
        write_statistics=True,
        use_dictionary=True,
    )
    assert hashlib.sha256((tmp_path / "ref.parquet").read_bytes()).hexdigest() == in_memory
    meta_a = pq.read_metadata(tmp_path / "a" / "lake/v/t/date=2026-09-26/part-0.parquet")
    assert meta_a.num_row_groups == 7


def test_seen_keys_is_exact_then_bounded() -> None:
    s = SeenKeys(exact_max=3, window_ns=100)
    assert s.add("a", 0) and s.add("b", 10) and not s.add("a", 20)
    assert s.add("c", 50) and len(s) == 3
    assert s.add("d", 150)  # over the limit: keys older than 150 - 100 are forgotten
    assert len(s) == 2 and not s.add("c", 151) and not s.add("d", 152)
    assert s.add("a", 153)  # forgotten: accepted again (only far-apart repeats)
