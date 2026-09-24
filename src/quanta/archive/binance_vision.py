"""Backfill from Binance's public archive (data.binance.vision), USDⓈ-M futures.

* Files are mirrored under ``<data_dir>/archive/binance_vision/<same path as the URL>`` and
  verified against the published ``.CHECKSUM`` (sha256) before being kept — a mismatch is
  never stored. Mirrored zips are the immutable source; Parquet is derived from them.
* A missing file (HTTP 404) is reported, not an error: datasets start at different dates and
  some were discontinued (UM bookTicker ended 2024-03; see research report §8.1).
* Conversion writes ``<data_dir>/lake/binance_vision_um/<dataset>/<date|month>=…/part-0.parquet``
  with nanosecond UTC timestamps (ms and µs inputs both handled) and lineage metadata.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import zipfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import aiohttp
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from quanta.core.log import get_logger

log = get_logger(__name__)

BASE_URL = "https://data.binance.vision"
PREFIX = "data/futures/um"
KLINE_DATASETS = ("klines", "markPriceKlines", "indexPriceKlines", "premiumIndexKlines")
MONTHLY_ONLY = ("fundingRate",)

_KLINE_COLS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
]
# column names, types, and which columns are timestamps (epoch ms/µs) or datetime strings
COLUMNS: dict[str, list[tuple[str, pa.DataType]]] = {
    "aggTrades": [
        ("agg_trade_id", pa.int64()),
        ("price", pa.float64()),
        ("quantity", pa.float64()),
        ("first_trade_id", pa.int64()),
        ("last_trade_id", pa.int64()),
        ("transact_time", pa.int64()),
        ("is_buyer_maker", pa.bool_()),
    ],
    "trades": [
        ("id", pa.int64()),
        ("price", pa.float64()),
        ("qty", pa.float64()),
        ("quote_qty", pa.float64()),
        ("time", pa.int64()),
        ("is_buyer_maker", pa.bool_()),
    ],
    "klines": [
        (c, pa.int64() if c in ("open_time", "close_time", "count") else pa.float64())
        for c in _KLINE_COLS
    ],
    "bookDepth": [
        ("timestamp", pa.string()),
        ("percentage", pa.float64()),
        ("depth", pa.float64()),
        ("notional", pa.float64()),
    ],
    "metrics": [
        ("create_time", pa.string()),
        ("symbol", pa.string()),
        ("sum_open_interest", pa.float64()),
        ("sum_open_interest_value", pa.float64()),
        ("count_toptrader_long_short_ratio", pa.float64()),
        ("sum_toptrader_long_short_ratio", pa.float64()),
        ("count_long_short_ratio", pa.float64()),
        ("sum_taker_long_short_vol_ratio", pa.float64()),
    ],
    "fundingRate": [
        ("calc_time", pa.int64()),
        ("funding_interval_hours", pa.int64()),
        ("last_funding_rate", pa.float64()),
    ],
}
EPOCH_COLS = {"transact_time", "time", "open_time", "close_time", "calc_time"}
DATETIME_COLS = {"timestamp", "create_time"}


def columns_for(dataset: str) -> list[tuple[str, pa.DataType]]:
    return COLUMNS["klines" if dataset in KLINE_DATASETS else dataset]


def archive_path(dataset: str, symbol: str, period: str, stamp: str, interval: str = "1m") -> str:
    if dataset in KLINE_DATASETS:
        return f"{PREFIX}/{period}/{dataset}/{symbol}/{interval}/{symbol}-{interval}-{stamp}.zip"
    return f"{PREFIX}/{period}/{dataset}/{symbol}/{symbol}-{dataset}-{stamp}.zip"


@dataclass(slots=True)
class FetchResult:
    url: str
    status: str  # downloaded | cached | missing | checksum_mismatch | error
    path: Path | None = None
    sha256: str | None = None
    error: str | None = None


@dataclass(slots=True)
class BackfillReport:
    dataset: str
    symbol: str
    results: list[FetchResult] = field(default_factory=list)
    converted_rows: dict[str, int] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.results:
            out[r.status] = out.get(r.status, 0) + 1
        return out

    @property
    def ok(self) -> bool:
        return not any(r.status in ("checksum_mismatch", "error") for r in self.results)


def _stamps(dataset: str, start: date, end: date) -> tuple[str, list[str]]:
    if dataset in MONTHLY_ONLY:
        months: list[str] = []
        d = start.replace(day=1)
        while d <= end:
            months.append(d.strftime("%Y-%m"))
            d = (d + timedelta(days=32)).replace(day=1)
        return "monthly", months
    days = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    return "daily", days


async def fetch(
    session: aiohttp.ClientSession, base_url: str, rel: str, mirror: Path, attempts: int = 3
) -> FetchResult:
    url = f"{base_url.rstrip('/')}/{rel}"
    dest = mirror / rel
    if dest.exists():
        return FetchResult(url, "cached", dest, _sha256_bytes(dest.read_bytes()))
    last_err = ""
    for attempt in range(attempts):
        try:
            async with session.get(url) as resp:
                if resp.status == 404:
                    return FetchResult(url, "missing")
                resp.raise_for_status()
                data = await resp.read()
            async with session.get(url + ".CHECKSUM") as resp:
                resp.raise_for_status()
                expected = (await resp.text()).split()[0].strip().lower()
            digest = _sha256_bytes(data)
            if digest != expected:
                log.error("archive_checksum_mismatch", url=url)
                return FetchResult(url, "checksum_mismatch", error=f"{digest} != {expected}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, dest)
            return FetchResult(url, "downloaded", dest, digest)
        except (aiohttp.ClientError, TimeoutError) as exc:
            last_err = repr(exc)
            await asyncio.sleep(0.5 * 2**attempt)
    return FetchResult(url, "error", error=last_err)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_csv_zip(path: Path, dataset: str) -> pa.Table:
    cols = columns_for(dataset)
    names = [c for c, _ in cols]
    with zipfile.ZipFile(path) as zf:
        raw = zf.read(zf.namelist()[0])
    first = raw.split(b"\n", 1)[0].decode("utf-8", "replace").strip().lower()
    has_header = first.split(",")[0] in names or not first[:1].isdigit()
    return pacsv.read_csv(
        io.BytesIO(raw),
        read_options=pacsv.ReadOptions(column_names=names, skip_rows=1 if has_header else 0),
        convert_options=pacsv.ConvertOptions(
            column_types=dict(cols),
            true_values=["true", "True", "TRUE"],
            false_values=["false", "False", "FALSE"],
        ),
    )


def _epoch_to_ts(arr: pa.ChunkedArray) -> pa.ChunkedArray:
    """Epoch ms or µs (Binance moved some archives to µs) → ns UTC timestamps."""
    as_int = arr.cast(pa.int64())
    is_us = pc.greater(as_int, 10**15)
    ns = pc.if_else(is_us, pc.multiply(as_int, 1_000), pc.multiply(as_int, 1_000_000))
    return ns.cast(pa.timestamp("ns", tz="UTC"))


def normalize_table(table: pa.Table, dataset: str, symbol: str) -> pa.Table:
    out: dict[str, Any] = {"symbol": pa.array([symbol] * table.num_rows, pa.string())}
    for name in table.column_names:
        if name == "ignore" or (name == "symbol" and dataset == "metrics"):
            continue
        col = table.column(name)
        if name in EPOCH_COLS:
            col = _epoch_to_ts(col)
        elif name in DATETIME_COLS:
            col = pc.assume_timezone(pc.strptime(col, "%Y-%m-%d %H:%M:%S", "ns"), "UTC")
        out[name] = col
    return pa.table(out)


def convert(
    result: FetchResult, dataset: str, symbol: str, stamp: str, root: Path, interval: str = "1m"
) -> int:
    assert result.path is not None
    table = normalize_table(read_csv_zip(result.path, dataset), dataset, symbol)
    table = table.replace_schema_metadata(
        {
            b"quanta.source_url": result.url.encode(),
            b"quanta.source_sha256": (result.sha256 or "").encode(),
        }
    )
    name = f"{dataset}_{interval}" if dataset in KLINE_DATASETS else dataset
    part = "month" if dataset in MONTHLY_ONLY else "date"
    dest = root / "lake" / "binance_vision_um" / name / f"symbol={symbol}" / f"{part}={stamp}"
    dest.mkdir(parents=True, exist_ok=True)
    tmp = dest / "part-0.parquet.tmp"
    pq.write_table(table, tmp, compression="zstd", compression_level=3)
    os.replace(tmp, dest / "part-0.parquet")
    return int(table.num_rows)


async def backfill(
    root: Path,
    dataset: str,
    symbol: str,
    start: date,
    end: date,
    *,
    interval: str = "1m",
    convert_parquet: bool = True,
    concurrency: int = 4,
    base_url: str = BASE_URL,
) -> BackfillReport:
    if dataset not in (*COLUMNS, *KLINE_DATASETS):
        raise ValueError(f"unsupported dataset {dataset!r}")
    period, stamps = _stamps(dataset, start, end)
    mirror = root / "archive" / "binance_vision"
    report = BackfillReport(dataset, symbol)
    sem = asyncio.Semaphore(concurrency)
    timeout = aiohttp.ClientTimeout(total=600)

    async with aiohttp.ClientSession(timeout=timeout) as session:

        async def one(stamp: str) -> tuple[str, FetchResult]:
            async with sem:
                rel = archive_path(dataset, symbol, period, stamp, interval)
                return stamp, await fetch(session, base_url, rel, mirror)

        results = await asyncio.gather(*(one(s) for s in stamps))
    for stamp, res in results:
        report.results.append(res)
        if convert_parquet and res.status in ("downloaded", "cached"):
            report.converted_rows[stamp] = await asyncio.to_thread(
                convert, res, dataset, symbol, stamp, root, interval
            )
    return report
