"""Completeness check: recorded aggTrades vs Binance's official daily archive.

data.binance.vision publishes every USDⓈ-M aggTrade per symbol per UTC day (≈1 day lag).
This is our ground truth for trade completeness (plan §8.4, Phase 0 success criterion):
within the window we were recording, every archived aggregate trade id must be present in
our capture with identical fields.

The comparison is columnar (pyarrow): a busy symbol has millions of aggregate trades per day,
and the daily job runs on a shared server, so neither side is held as Python objects.
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from quanta.tools.raw_reader import day_bounds_ms, days_around, iter_records, segment_files
from quanta.venues.binance_usdm import messages as msg

ARCHIVE_BASE = "https://data.binance.vision/data/futures/um/daily/aggTrades"
COLUMNS = ("id", "price", "qty", "first_id", "last_id", "time_ms", "buyer_maker")
_TYPES = (pa.int64(), pa.float64(), pa.float64(), pa.int64(), pa.int64(), pa.int64(), pa.bool_())


@dataclass(frozen=True, slots=True)
class TradeRow:
    agg_id: int
    price: Decimal
    qty: Decimal
    first_id: int
    last_id: int
    time_ms: int
    buyer_maker: bool


@dataclass(slots=True)
class AggTradeReport:
    symbol: str
    day: str
    archive_count: int = 0
    recorded_count: int = 0
    window_first_id: int | None = None
    window_last_id: int | None = None
    archive_in_window: int = 0
    missing_in_window: int = 0
    missing_ranges: list[tuple[int, int]] = field(default_factory=list)
    extra_ids: int = 0
    mismatched: int = 0
    mismatch_examples: list[dict[str, object]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            self.recorded_count > 0
            and self.missing_in_window == 0
            and self.mismatched == 0
            and self.extra_ids == 0
        )


def archive_url(symbol: str, day: date) -> str:
    return f"{ARCHIVE_BASE}/{symbol}/{symbol}-aggTrades-{day.isoformat()}.zip"


class ArchiveMissing(OSError):
    """The day's archive is not published (yet): HTTP 404."""


def fetch_archive(symbol: str, day: date, cache_dir: Path) -> tuple[Path, str]:
    """Checksum-verified download via the shared archive mirror (quanta.archive).
    Returns (path, sha256); raises ArchiveMissing (404) or OSError."""
    import asyncio

    import aiohttp

    from quanta.archive.binance_vision import BASE_URL, archive_path, fetch

    async def run() -> tuple[Path, str]:
        rel = archive_path("aggTrades", symbol, "daily", day.isoformat())
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600)) as session:
            res = await fetch(session, BASE_URL, rel, cache_dir)
        if res.status == "missing":
            raise ArchiveMissing(f"archive {res.url}: not published yet")
        if res.path is None:
            raise OSError(f"archive {res.url}: {res.status} {res.error or ''}".strip())
        return res.path, res.sha256 or ""

    return asyncio.run(run())


def download_archive(symbol: str, day: date, cache_dir: Path) -> Path:
    return fetch_archive(symbol, day, cache_dir)[0]


def read_archive(path: Path) -> dict[int, TradeRow]:
    rows: dict[int, TradeRow] = {}
    with zipfile.ZipFile(path) as zf:
        name = zf.namelist()[0]
        with zf.open(name) as fh:
            for rec in csv.reader(io.TextIOWrapper(fh, encoding="utf-8")):
                if not rec or not rec[0].isdigit():
                    continue  # header line (present in newer files)
                row = TradeRow(
                    int(rec[0]),
                    Decimal(rec[1]),
                    Decimal(rec[2]),
                    int(rec[3]),
                    int(rec[4]),
                    int(rec[5]),
                    rec[6].strip().lower() == "true",
                )
                rows[row.agg_id] = row
    return rows


def read_archive_table(path: Path) -> pa.Table:
    from quanta.archive.binance_vision import read_csv_zip

    return read_csv_zip(path, "aggTrades").rename_columns(list(COLUMNS))


def _market_files(data_dir: Path, day: date) -> list[Path]:
    """The day's market segments plus the first one of the next day (late arrivals)."""
    files = segment_files(data_dir, "binance_usdm", "market", [day])
    return files + segment_files(data_dir, "binance_usdm", "market", [day + timedelta(days=1)])[:1]


def read_recorded_tables(data_dir: Path, symbols: list[str], day: date) -> dict[str, pa.Table]:
    """Recorded aggTrades of ``day`` (exchange time) per symbol, one pass over the market
    channel, compact columns, sorted by id and de-duplicated (rotation overlap)."""
    from array import array

    streams = {f"{s.lower()}@aggTrade": s for s in symbols}
    cols: dict[str, list[array[Any]]] = {
        s: [array("q"), array("d"), array("d"), array("q"), array("q"), array("q"), array("b")]
        for s in symbols
    }
    start_ms, end_ms = day_bounds_ms(day)
    for rec in iter_records(_market_files(data_dir, day)):
        if rec.k != "ws":
            continue
        env = msg.decode_envelope(bytes(rec.p))
        sym = streams.get(env.stream)
        if sym is None:
            continue
        tr = msg.decode_agg_trade(env.data)
        if start_ms <= tr.T < end_ms:
            c = cols[sym]
            c[0].append(tr.a)
            c[1].append(float(tr.p))
            c[2].append(float(tr.q))
            c[3].append(tr.f)
            c[4].append(tr.l)
            c[5].append(tr.T)
            c[6].append(1 if tr.m else 0)
    return {s: _dedup(_table(c)) for s, c in cols.items()}


def _column(values: Any, typ: pa.DataType) -> pa.Array:
    from array import array

    if not isinstance(values, array):
        return pa.array(values, type=typ)
    # zero-copy view of the compact buffer (bools are collected as int8)
    raw = pa.Array.from_buffers(
        pa.int8() if typ == pa.bool_() else typ, len(values), [None, pa.py_buffer(values)]
    )
    return pc.not_equal(raw, 0) if typ == pa.bool_() else raw


def _table(values: list[Any]) -> pa.Table:
    arrays = [_column(v, t) for v, t in zip(values, _TYPES, strict=True)]
    return pa.table(dict(zip(COLUMNS, arrays, strict=True)))


def _dedup(t: pa.Table) -> pa.Table:
    if t.num_rows < 2:
        return t
    t = t.sort_by("id")
    ids = t.column("id").combine_chunks()
    keep = pc.not_equal(ids.slice(1), ids.slice(0, len(ids) - 1))
    return t.filter(pa.concat_arrays([pa.array([True]), keep]))


def _rows_table(rows: dict[int, TradeRow]) -> pa.Table:
    vals: list[list[Any]] = [[] for _ in COLUMNS]
    for r in rows.values():
        for i, v in enumerate(
            (r.agg_id, float(r.price), float(r.qty), r.first_id, r.last_id, r.time_ms)
        ):
            vals[i].append(v)
        vals[6].append(r.buyer_maker)
    return _dedup(_table(vals))


def read_recorded(data_dir: Path, symbol: str, day: date) -> dict[int, TradeRow]:
    start_ms, end_ms = day_bounds_ms(day)
    stream = f"{symbol.lower()}@aggTrade"
    files = segment_files(data_dir, "binance_usdm", "market", days_around(day, 0, 1))
    out: dict[int, TradeRow] = {}
    for rec in iter_records(files):
        if rec.k != "ws":
            continue
        env = msg.decode_envelope(bytes(rec.p))
        if env.stream != stream:
            continue
        tr = msg.decode_agg_trade(env.data)
        if start_ms <= tr.T < end_ms:
            out[tr.a] = TradeRow(tr.a, Decimal(tr.p), Decimal(tr.q), tr.f, tr.l, tr.T, tr.m)
    return out


def _ranges(ids: list[int]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for i in ids:
        if out and i == out[-1][1] + 1:
            out[-1] = (out[-1][0], i)
        else:
            out.append((i, i))
    return out


def compare(
    symbol: str, day: date, archive: dict[int, TradeRow], recorded: dict[int, TradeRow]
) -> AggTradeReport:
    return compare_tables(symbol, day, _rows_table(archive), _rows_table(recorded))[0]


def compare_tables(
    symbol: str, day: date, archive: pa.Table, recorded: pa.Table
) -> tuple[AggTradeReport, pa.Table]:
    """Report + the archive rows missing from the capture inside the recorded id window
    (sorted by id). Prices/quantities compare as float64: parsing equal decimal strings
    gives identical doubles."""
    rep = AggTradeReport(symbol, day.isoformat(), archive.num_rows, recorded.num_rows)
    empty = archive.schema.empty_table()
    if recorded.num_rows == 0:
        return rep, empty
    bounds = pc.min_max(recorded.column("id")).as_py()
    lo, hi = int(bounds["min"]), int(bounds["max"])
    rep.window_first_id, rep.window_last_id = lo, hi
    ids = archive.column("id")
    win = archive.filter(pc.and_(pc.greater_equal(ids, lo), pc.less_equal(ids, hi)))
    rep.archive_in_window = win.num_rows
    ours = recorded.rename_columns(["id"] + [f"r_{c}" for c in COLUMNS[1:]])
    joined = win.join(ours, keys="id", join_type="left outer")
    present = pc.is_valid(joined.column("r_time_ms"))
    missing = joined.filter(pc.invert(present)).select(list(COLUMNS)).sort_by("id")
    rep.missing_in_window = missing.num_rows
    rep.missing_ranges = _ranges(missing.column("id").to_pylist())[:50]
    both = joined.filter(present)
    differs = pc.not_equal(both.column(COLUMNS[1]), both.column(f"r_{COLUMNS[1]}"))
    for c in COLUMNS[2:]:
        differs = pc.or_(differs, pc.not_equal(both.column(c), both.column(f"r_{c}")))
    bad = both.filter(differs).sort_by("id")
    rep.mismatched = bad.num_rows
    for row in bad.slice(0, 10).to_pylist():
        rep.mismatch_examples.append(
            {
                "id": row["id"],
                "archive": {c: row[c] for c in COLUMNS[1:]},
                "recorded": {c: row[f"r_{c}"] for c in COLUMNS[1:]},
            }
        )
    rep.extra_ids = recorded.join(archive.select(["id"]), keys="id", join_type="left anti").num_rows
    return rep, missing


def verify(data_dir: Path, symbol: str, day: date, cache_dir: Path) -> AggTradeReport:
    archive = read_archive_table(download_archive(symbol, day, cache_dir))
    recorded = read_recorded_tables(data_dir, [symbol], day)[symbol]
    return compare_tables(symbol, day, archive, recorded)[0]
