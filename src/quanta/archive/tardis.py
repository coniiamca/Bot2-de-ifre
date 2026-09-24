"""Tardis.dev downloadable CSV datasets → verified mirror + Parquet.

Facts (docs.tardis.dev "Downloadable CSV files", responses checked 2026-09-24):
* ``https://datasets.tardis.dev/v1/{exchange}/{data_type}/{YYYY}/{MM}/{DD}/{SYMBOL}.csv.gz``
* The **first day of each month** downloads without an API key; other days answer HTTP 401
  unless ``Authorization: Bearer <key>`` is sent. A key is read from a secret file only.
* Anonymous downloads have a **data-transfer quota**: HTTP 429, ``{"code": 277, "message":
  "This account has reached its data transfer limit."}`` with ``Retry-After`` in seconds
  (observed ~4.7 h). A run stops cleanly at the quota and resumes where it left off.
* Responses carry ``x-md5`` (checksum of the .gz) and ``x-dataset-size``; HEAD and Range
  requests are not supported. Integrity = md5 matches *and* the gzip stream decompresses
  completely (CRC32 + length trailer) *and* parses as CSV. Only verified files are kept.
* Timestamps are µs since epoch: ``timestamp`` (exchange) and ``local_timestamp`` (arrival at
  Tardis's collector). Renamed to ``ts_exchange`` / ``ts_arrival`` (ns, UTC) like our lake
  (ADR-009); ``funding_timestamp`` → ``ts_funding``. Other columns keep Tardis's names.

Layout:
* mirror  ``<data>/archive/tardis/<exchange>/<type>/<YYYY-MM-DD>/<SYMBOL>.csv.gz`` plus a
  sidecar ``….csv.gz.json`` (url, md5, sha256, bytes, rows, time range) — the sidecar is
  written last, so its presence means "verified".
* Parquet ``<data>/lake/tardis/<exchange>/<type>/symbol=<SYMBOL>/date=<YYYY-MM-DD>/part-0.parquet``
  converted in one streaming pass (L2 days are hundreds of MB compressed).
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import hashlib
import json
import os
import time
import zlib
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import aiohttp
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from quanta.core.log import get_logger

log = get_logger(__name__)

BASE_URL = "https://datasets.tardis.dev/v1"
CONVERTER_VERSION = "1"
EXCHANGES = ("binance-futures", "bybit", "deribit", "okex-swap")
SMALL_TYPES = ("trades", "liquidations", "derivative_ticker")
L2_TYPES = ("book_snapshot_25", "incremental_book_L2")
DATA_TYPES = (*SMALL_TYPES, "quotes", "book_snapshot_5", *L2_TYPES)

_RENAME = {"timestamp": "ts_exchange", "local_timestamp": "ts_arrival"}
_RENAME_TS = {"funding_timestamp": "ts_funding"}
_STRING_COLS = frozenset({"exchange", "symbol", "side", "id"})
_BOOL_COLS = frozenset({"is_snapshot"})
_US_COLS = frozenset({"timestamp", "local_timestamp", "funding_timestamp"})
_CHUNK = 1 << 20


class QuotaExceeded(Exception):
    def __init__(self, retry_after_s: float, message: str) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


@dataclass(slots=True)
class FileResult:
    exchange: str
    data_type: str
    symbol: str
    day: str
    # downloaded | empty | cached | missing | needs_key | unauthorized | corrupt | error | quota
    status: str
    bytes: int = 0
    rows: int | None = None
    error: str | None = None


@dataclass(slots=True)
class ImportReport:
    results: list[FileResult] = field(default_factory=list)
    quota_retry_after_s: float | None = None

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.results:
            out[r.status] = out.get(r.status, 0) + 1
        return out

    @property
    def downloaded_bytes(self) -> int:
        return sum(r.bytes for r in self.results if r.status == "downloaded")

    @property
    def ok(self) -> bool:
        return not any(r.status in ("corrupt", "error", "unauthorized") for r in self.results)


def dataset_url(base_url: str, exchange: str, data_type: str, day: date, symbol: str) -> str:
    return (
        f"{base_url.rstrip('/')}/{exchange}/{data_type}/"
        f"{day.year:04d}/{day.month:02d}/{day.day:02d}/{symbol}.csv.gz"
    )


def mirror_path(root: Path, exchange: str, data_type: str, day: date, symbol: str) -> Path:
    return root / "archive" / "tardis" / exchange / data_type / day.isoformat() / f"{symbol}.csv.gz"


def parquet_path(root: Path, exchange: str, data_type: str, day: date, symbol: str) -> Path:
    return (
        root
        / "lake"
        / "tardis"
        / exchange
        / data_type
        / f"symbol={symbol}"
        / f"date={day.isoformat()}"
        / "part-0.parquet"
    )


def month_starts(first: str, last: str) -> list[date]:
    """``"2020-01"``, ``"2026-09"`` → first day of every month in between (inclusive)."""
    y, m = (int(x) for x in first.split("-"))
    ly, lm = (int(x) for x in last.split("-"))
    out: list[date] = []
    while (y, m) <= (ly, lm):
        out.append(date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def read_api_key(path: Path | None) -> str | None:
    if path is None:
        return None
    key = path.read_text(encoding="utf-8").strip()
    return key or None


# -- conversion (sync, runs in a worker thread) ---------------------------------------------
def _column_types(header: list[str]) -> dict[str, pa.DataType]:
    types: dict[str, pa.DataType] = {}
    for name in header:
        if name in _US_COLS:
            types[name] = pa.int64()
        elif name in _STRING_COLS:
            types[name] = pa.string()
        elif name in _BOOL_COLS:
            types[name] = pa.bool_()
        else:
            types[name] = pa.float64()
    return types


def _out_name(name: str) -> str:
    return _RENAME.get(name) or _RENAME_TS.get(name) or name


def _out_schema(header: list[str], metadata: dict[bytes, bytes]) -> pa.Schema:
    types = _column_types(header)
    fields = [
        pa.field(_out_name(n), pa.timestamp("ns", tz="UTC") if n in _US_COLS else types[n])
        for n in header
    ]
    return pa.schema(fields, metadata=metadata)


def _transform(batch: pa.RecordBatch, schema: pa.Schema) -> pa.RecordBatch:
    cols = []
    for name, col in zip(batch.schema.names, batch.columns, strict=True):
        if name in _US_COLS:
            col = pc.multiply(col, 1000).cast(pa.timestamp("ns", tz="UTC"))
        cols.append(col)
    return pa.RecordBatch.from_arrays(cols, schema=schema)


@dataclass(slots=True)
class Converted:
    rows: int
    first_arrival: str | None
    last_arrival: str | None
    columns: list[str]


def convert_gz(src: Path, dest: Path | None, metadata: dict[str, str]) -> Converted:
    """One streaming pass over a Tardis .csv.gz: verifies the gzip stream end to end and
    parses every row; writes Parquet to ``dest`` (atomically) unless ``dest`` is None.
    A valid gzip holding zero bytes (Tardis's answer for "no data that day", seen for Deribit
    liquidations) returns ``columns == []`` and writes nothing.
    Raises ``ValueError`` on corrupt/truncated gzip or unparsable CSV."""
    try:
        with gzip.open(src, "rb") as fh:
            header = fh.readline().decode("utf-8").strip().split(",")
            if header == [""]:
                if fh.read(1):
                    raise ValueError("data without a CSV header")
                return Converted(0, None, None, [])
            fh.seek(0)
            reader = pacsv.open_csv(
                fh,
                read_options=pacsv.ReadOptions(block_size=16 << 20),
                convert_options=pacsv.ConvertOptions(
                    column_types=_column_types(header),
                    strings_can_be_null=True,
                    true_values=["true", "True", "TRUE"],
                    false_values=["false", "False", "FALSE"],
                ),
            )
            meta = {f"quanta.{k}".encode(): v.encode() for k, v in metadata.items()}
            schema = _out_schema(reader.schema.names, meta)
            tmp = dest.with_name(dest.name + ".tmp") if dest else None
            writer = None
            if tmp is not None:
                tmp.parent.mkdir(parents=True, exist_ok=True)
                writer = pq.ParquetWriter(tmp, schema, compression="zstd", compression_level=3)
            rows = 0
            first: int | None = None
            last: int | None = None
            try:
                for batch in reader:
                    out = _transform(batch, schema)
                    rows += out.num_rows
                    if "ts_arrival" in schema.names and out.num_rows:
                        arr = out.column(schema.get_field_index("ts_arrival")).cast(pa.int64())
                        mn, mx = pc.min(arr).as_py(), pc.max(arr).as_py()
                        first = mn if first is None or (mn is not None and mn < first) else first
                        last = mx if last is None or (mx is not None and mx > last) else last
                    if writer is not None:
                        writer.write_batch(out)
            finally:
                if writer is not None:
                    writer.close()
    except (OSError, EOFError, zlib.error, pa.ArrowInvalid, UnicodeDecodeError) as exc:
        # gzip.BadGzipFile is an OSError; a truncated stream raises EOFError
        if dest is not None:
            with contextlib.suppress(FileNotFoundError):
                dest.with_name(dest.name + ".tmp").unlink()
        raise ValueError(f"{type(exc).__name__}: {exc}") from exc
    if tmp is not None and dest is not None:
        os.replace(tmp, dest)

    def iso(ns: int | None) -> str | None:
        return None if ns is None else datetime.fromtimestamp(ns / 1e9, UTC).isoformat()

    return Converted(rows, iso(first), iso(last), list(schema.names))


# -- download -------------------------------------------------------------------------------
async def _download(
    session: aiohttp.ClientSession, url: str, tmp: Path, api_key: str | None
) -> tuple[int, str, str]:
    """Stream ``url`` to ``tmp`` → (bytes, md5, sha256). Raises for HTTP errors (status in
    ``aiohttp.ClientResponseError.status``) and ``QuotaExceeded`` for HTTP 429."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with session.get(url, headers=headers) as r:
        if r.status == 429:
            body = await r.text()
            try:
                retry = float(r.headers.get("Retry-After", "60"))
            except ValueError:
                retry = 60.0
            raise QuotaExceeded(retry, body[:300])
        if r.status != 200:
            await r.read()
            raise aiohttp.ClientResponseError(
                r.request_info, (), status=r.status, message=r.reason or ""
            )
        expected_md5 = r.headers.get("x-md5", "").strip().strip('"').lower() or None
        expected_len = r.content_length
        md5, sha = hashlib.md5(usedforsecurity=False), hashlib.sha256()
        size = 0
        tmp.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open("wb") as out:
            async for chunk in r.content.iter_chunked(_CHUNK):
                out.write(chunk)
                md5.update(chunk)
                sha.update(chunk)
                size += len(chunk)
    if expected_len is not None and size != expected_len:
        raise ValueError(f"truncated download: {size} of {expected_len} bytes")
    if expected_md5 and md5.hexdigest() != expected_md5:
        raise ValueError(f"md5 mismatch: {md5.hexdigest()} != {expected_md5}")
    return size, md5.hexdigest(), sha.hexdigest()


async def import_file(
    session: aiohttp.ClientSession,
    root: Path,
    exchange: str,
    data_type: str,
    day: date,
    symbol: str,
    *,
    api_key: str | None = None,
    convert: bool = True,
    base_url: str = BASE_URL,
    attempts: int = 3,
    max_wait_s: float = 120.0,
) -> FileResult:
    res = FileResult(exchange, data_type, symbol, day.isoformat(), "error")
    if day.day != 1 and not api_key:
        res.status = "needs_key"
        return res
    url = dataset_url(base_url, exchange, data_type, day, symbol)
    mirror = mirror_path(root, exchange, data_type, day, symbol)
    sidecar = mirror.with_name(mirror.name + ".json")
    dest = parquet_path(root, exchange, data_type, day, symbol) if convert else None
    meta = {
        "source": "tardis",
        "source_url": url,
        "converter_version": CONVERTER_VERSION,
        "exchange": exchange,
        "data_type": data_type,
        "symbol": symbol,
        "date": day.isoformat(),
    }

    if sidecar.exists() and mirror.exists():
        info = json.loads(sidecar.read_text(encoding="utf-8"))
        res.status, res.bytes, res.rows = "cached", int(info["bytes"]), info.get("rows")
        if dest is not None and info.get("columns") and not dest.exists():
            meta["source_sha256"] = info["sha256"]
            try:
                await asyncio.to_thread(convert_gz, mirror, dest, meta)
            except ValueError as exc:
                res.status, res.error = "corrupt", str(exc)
        return res

    tmp = mirror.with_name(mirror.name + ".part")
    for attempt in range(attempts):
        try:
            size, md5, sha = await _download(session, url, tmp, api_key)
            meta["source_sha256"] = sha
            conv = await asyncio.to_thread(convert_gz, tmp, dest, meta)
        except QuotaExceeded as exc:
            tmp.unlink(missing_ok=True)
            if exc.retry_after_s <= max_wait_s and attempt + 1 < attempts:
                log.warning("tardis_rate_limited", url=url, retry_after_s=exc.retry_after_s)
                await asyncio.sleep(exc.retry_after_s)
                continue
            raise
        except aiohttp.ClientResponseError as exc:
            tmp.unlink(missing_ok=True)
            if exc.status == 404:
                res.status = "missing"
            elif exc.status in (401, 403):
                res.status, res.error = "unauthorized", f"HTTP {exc.status}"
            elif exc.status >= 500 and attempt + 1 < attempts:
                await asyncio.sleep(2.0 * 2**attempt)
                continue
            else:
                res.error = f"HTTP {exc.status}"
            return res
        except ValueError as exc:  # md5/length/gzip/CSV: the bytes are bad — retry once more
            tmp.unlink(missing_ok=True)
            res.status, res.error = "corrupt", str(exc)
            log.error("tardis_corrupt", url=url, error=str(exc), attempt=attempt)
            if attempt + 1 < attempts:
                continue
            return res
        except (aiohttp.ClientError, TimeoutError) as exc:
            tmp.unlink(missing_ok=True)
            res.status, res.error = "error", repr(exc)
            if attempt + 1 < attempts:
                await asyncio.sleep(2.0 * 2**attempt)
                continue
            return res
        os.replace(tmp, mirror)
        info = {
            "url": url,
            "bytes": size,
            "md5": md5,
            "sha256": sha,
            "rows": conv.rows,
            "columns": conv.columns,
            "first_arrival": conv.first_arrival,
            "last_arrival": conv.last_arrival,
            "downloaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "converter_version": CONVERTER_VERSION,
        }
        side_tmp = sidecar.with_name(sidecar.name + ".tmp")
        side_tmp.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
        os.replace(side_tmp, sidecar)
        res.status = "downloaded" if conv.columns else "empty"
        res.bytes, res.rows, res.error = size, conv.rows, None
        return res
    return res


@dataclass(frozen=True, slots=True)
class Job:
    exchange: str
    data_type: str
    symbol: str
    day: date


def plan_jobs(
    exchanges: Iterable[str],
    symbols: dict[str, list[str]],
    days: list[date],
    types: Iterable[str],
    l2_days: Iterable[date] = (),
) -> list[Job]:
    """Small types for every day first (cheap, most useful), then L2 types for ``l2_days``."""
    small = [t for t in types if t not in L2_TYPES]
    l2 = [t for t in types if t in L2_TYPES] or list(L2_TYPES)
    jobs = [
        Job(ex, t, s, d)
        for d in sorted(days)
        for ex in exchanges
        for t in small
        for s in symbols.get(ex, [])
    ]
    jobs += [
        Job(ex, t, s, d)
        for d in sorted(set(l2_days))
        for ex in exchanges
        for t in l2
        for s in symbols.get(ex, [])
    ]
    return jobs


async def run_import(
    root: Path,
    jobs: list[Job],
    *,
    api_key: str | None = None,
    convert: bool = True,
    concurrency: int = 2,
    base_url: str = BASE_URL,
    max_wait_s: float = 120.0,
) -> ImportReport:
    """Runs ``jobs`` with bounded concurrency. Stops starting new downloads at the first
    quota error (the rest are reported as ``quota``); re-running resumes from the cache."""
    report = ImportReport()
    sem = asyncio.Semaphore(concurrency)
    stop = asyncio.Event()
    started = time.monotonic()
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=120)

    async with aiohttp.ClientSession(timeout=timeout) as session:

        async def one(job: Job) -> FileResult:
            async with sem:
                if stop.is_set():
                    return FileResult(
                        job.exchange, job.data_type, job.symbol, job.day.isoformat(), "quota"
                    )
                try:
                    r = await import_file(
                        session,
                        root,
                        job.exchange,
                        job.data_type,
                        job.day,
                        job.symbol,
                        api_key=api_key,
                        convert=convert,
                        base_url=base_url,
                        max_wait_s=max_wait_s,
                    )
                except QuotaExceeded as exc:
                    stop.set()
                    report.quota_retry_after_s = exc.retry_after_s
                    log.warning("tardis_quota_reached", retry_after_s=exc.retry_after_s)
                    return FileResult(
                        job.exchange,
                        job.data_type,
                        job.symbol,
                        job.day.isoformat(),
                        "quota",
                        error=str(exc),
                    )
                log.info("tardis_file", **asdict(r))
                return r

        report.results = list(await asyncio.gather(*(one(j) for j in jobs)))
    log.info(
        "tardis_import_done",
        counts=report.counts(),
        downloaded_mb=round(report.downloaded_bytes / 1e6, 1),
        seconds=round(time.monotonic() - started, 1),
    )
    return report


def parse_symbols(items: list[str], exchanges: list[str]) -> dict[str, list[str]]:
    """``["BTCUSDT"]`` → the same symbol on every exchange; ``["deribit:BTC-PERPETUAL"]`` →
    only there. Tardis symbols are upper-case."""
    out: dict[str, list[str]] = {ex: [] for ex in exchanges}
    for item in items:
        ex, sep, sym = item.partition(":")
        targets = [ex] if sep else exchanges
        sym = (sym if sep else ex).strip().upper()
        for t in targets:
            if t not in out:
                raise ValueError(f"symbol {item!r} names an exchange not selected: {t!r}")
            if sym not in out[t]:
                out[t].append(sym)
    return out


def default_symbols(exchanges: list[str]) -> dict[str, list[str]]:
    defaults = {
        "binance-futures": ["BTCUSDT", "ETHUSDT"],
        "bybit": ["BTCUSDT", "ETHUSDT"],
        "deribit": ["BTC-PERPETUAL", "ETH-PERPETUAL"],
        "okex-swap": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
    }
    return {ex: list(defaults.get(ex, [])) for ex in exchanges}


def summary(report: ImportReport) -> dict[str, Any]:
    per: dict[str, dict[str, Any]] = {}
    for r in report.results:
        key = f"{r.exchange}/{r.data_type}/{r.symbol}"
        agg = per.setdefault(key, {"counts": {}, "mb": 0.0, "rows": 0})
        agg["counts"][r.status] = agg["counts"].get(r.status, 0) + 1
        agg["mb"] = round(agg["mb"] + r.bytes / 1e6, 1)
        agg["rows"] += r.rows or 0
    out: dict[str, Any] = {
        "counts": report.counts(),
        "downloaded_mb": round(report.downloaded_bytes / 1e6, 1),
        "datasets": per,
        "problems": [asdict(r) for r in report.results if r.status in ("corrupt", "error")][:20],
    }
    if report.quota_retry_after_s is not None:
        resume = datetime.now(UTC).timestamp() + report.quota_retry_after_s
        out["quota"] = {
            "retry_after_s": report.quota_retry_after_s,
            "resume_after": datetime.fromtimestamp(resume, UTC).isoformat(timespec="minutes"),
            "note": "Tardis anonymous data-transfer limit reached; re-run later to continue",
        }
    return out
