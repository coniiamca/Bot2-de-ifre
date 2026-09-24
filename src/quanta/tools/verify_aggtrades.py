"""Completeness check: recorded aggTrades vs Binance's official daily archive.

data.binance.vision publishes every USDⓈ-M aggTrade per symbol per UTC day (≈1 day lag).
This is our ground truth for trade completeness (plan §8.4, Phase 0 success criterion):
within the window we were recording, every archived aggregate trade id must be present in
our capture with identical fields.
"""

from __future__ import annotations

import csv
import hashlib
import io
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from quanta.tools.raw_reader import day_bounds_ms, days_around, iter_records, segment_files
from quanta.venues.binance_usdm import messages as msg

ARCHIVE_BASE = "https://data.binance.vision/data/futures/um/daily/aggTrades"


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


def download_archive(symbol: str, day: date, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    url = archive_url(symbol, day)
    dest = cache_dir / url.rsplit("/", 1)[-1]
    if not dest.exists():
        with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310 — fixed https URL
            data = resp.read()
        with urllib.request.urlopen(url + ".CHECKSUM", timeout=30) as resp:  # noqa: S310
            expected = resp.read().decode().split()[0]
        if hashlib.sha256(data).hexdigest() != expected:
            raise OSError(f"checksum mismatch for {url}")
        dest.write_bytes(data)
    return dest


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
    rep = AggTradeReport(symbol, day.isoformat(), len(archive), len(recorded))
    if not recorded:
        return rep
    lo, hi = min(recorded), max(recorded)
    rep.window_first_id, rep.window_last_id = lo, hi
    in_window = [i for i in archive if lo <= i <= hi]
    rep.archive_in_window = len(in_window)
    missing = sorted(i for i in in_window if i not in recorded)
    rep.missing_in_window = len(missing)
    rep.missing_ranges = _ranges(missing)[:50]
    rep.extra_ids = sum(1 for i in recorded if i not in archive)
    for i in in_window:
        ours = recorded.get(i)
        if ours is not None and ours != archive[i]:
            rep.mismatched += 1
            if len(rep.mismatch_examples) < 10:
                rep.mismatch_examples.append(
                    {"id": i, "archive": repr(archive[i]), "recorded": repr(ours)}
                )
    return rep


def verify(data_dir: Path, symbol: str, day: date, cache_dir: Path) -> AggTradeReport:
    archive = read_archive(download_archive(symbol, day, cache_dir))
    return compare(symbol, day, archive, read_recorded(data_dir, symbol, day))
