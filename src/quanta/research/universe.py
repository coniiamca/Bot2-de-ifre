"""Point-in-time trading universe from Binance's public archive (no survivorship bias).

Every USDⓈ-M USDT perpetual that ever had monthly daily klines in the archive — including
delisted ones — is a candidate. For month ``m`` the universe is the ``top_n`` candidates by
quote volume in month ``m − 1`` that had at least ``min_history_days`` of data before ``m``
began. Nothing about month ``m`` itself is used, so a coin that is delisted during ``m`` can
still be picked (and then simply stops being tradable, as it would have in reality).

Binance also lists perpetuals on stocks, ETFs, commodities and pre-IPO companies (from
late 2025). The hypotheses are about crypto markets, so a reviewed list of those
(``research/universe/non_crypto.txt``, built from the weekend/weekday volume fingerprint of
market-hours assets) is excluded.
"""

from __future__ import annotations

import asyncio
import csv
import gzip
import io
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import aiohttp
import pyarrow.compute as pc

from quanta.archive.binance_vision import (
    BASE_URL,
    LISTING_URL,
    PREFIX,
    FetchResult,
    Listed,
    fetch,
    list_keys,
    read_csv_zip,
)
from quanta.core.log import get_logger

log = get_logger(__name__)

SYMBOL_RE = re.compile(r"^[A-Z0-9]+USDT$")
STABLE_BASES = {
    "USDC", "BUSD", "TUSD", "FDUSD", "USDP", "DAI", "EUR", "GBP", "AEUR", "USDE", "USD1",
    "PYUSD", "RLUSD", "USTC", "UST",
}  # fmt: skip
UNIVERSE_HEADER = ["month", "rank", "symbol", "prev_month_quote_volume"]
MANIFEST_HEADER = ["key", "size", "md5", "sha256", "last_modified"]


def is_candidate(symbol: str) -> bool:
    return bool(SYMBOL_RE.match(symbol)) and symbol[: -len("USDT")] not in STABLE_BASES


def read_exclusions(path: Path) -> set[str]:
    """Symbols listed in an exclusion file (first column; ``#`` starts a comment)."""
    if not path.exists():
        return set()
    out: set[str] = set()
    for line in path.read_text().splitlines():
        body = line.split("#", 1)[0].split()
        if body:
            out.add(body[0])
    return out


def month_range(start: str, end: str) -> list[str]:
    """Inclusive list of YYYY-MM strings."""
    out: list[str] = []
    d = date.fromisoformat(start + "-01")
    stop = date.fromisoformat(end + "-01")
    while d <= stop:
        out.append(d.strftime("%Y-%m"))
        d = (d + timedelta(days=32)).replace(day=1)
    return out


def prev_month(m: str) -> str:
    d = date.fromisoformat(m + "-01") - timedelta(days=1)
    return d.strftime("%Y-%m")


def stamp_of(key: str) -> str:
    """``…/BTCUSDT-1d-2021-03.zip`` → ``2021-03``; daily files give YYYY-MM-DD."""
    name = key.rsplit("/", 1)[-1].removesuffix(".zip")
    m = re.search(r"(\d{4}-\d{2}(?:-\d{2})?)$", name)
    return m.group(1) if m else ""


@dataclass(frozen=True, slots=True)
class Used:
    listed: Listed
    result: FetchResult


async def fetch_listed(
    session: aiohttp.ClientSession,
    items: list[Listed],
    mirror: Path,
    base_url: str,
    concurrency: int,
) -> list[Used]:
    sem = asyncio.Semaphore(concurrency)

    async def one(item: Listed) -> Used:
        async with sem:
            res = await fetch(session, base_url, item.key, mirror, expected_md5=item.etag)
            return Used(item, res)

    return list(await asyncio.gather(*(one(i) for i in items)))


def write_manifest(path: Path, used: list[Used]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(MANIFEST_HEADER)
    for u in sorted(used, key=lambda x: x.listed.key):
        w.writerow(
            [u.listed.key, u.listed.size, u.listed.etag, u.result.sha256 or "",
             u.listed.last_modified]
        )  # fmt: skip
    data = buf.getvalue().encode()
    if path.suffix == ".gz":
        data = gzip.compress(data, mtime=0)
    path.write_bytes(data)


async def build_universe(
    root: Path,
    start: str,
    end: str,
    out_csv: Path,
    manifest: Path,
    *,
    top_n: int = 10,
    min_history_days: int = 60,
    exclude: set[str] | None = None,
    concurrency: int = 16,
    base_url: str = BASE_URL,
    listing_url: str = LISTING_URL,
) -> list[list[str]]:
    """Rank months ``start``..``end`` (YYYY-MM) and write the universe CSV + input manifest."""
    mirror = root / "archive" / "binance_vision"
    want = set(month_range(prev_month(start), end))
    timeout = aiohttp.ClientTimeout(total=600)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        _, prefixes = await list_keys(
            session, f"{PREFIX}/monthly/klines/", delimiter="/", listing_url=listing_url
        )
        symbols = [p.rstrip("/").rsplit("/", 1)[-1] for p in prefixes]
        symbols = sorted(s for s in symbols if is_candidate(s) and s not in (exclude or set()))
        log.info("universe_candidates", symbols=len(symbols))
        sem = asyncio.Semaphore(concurrency)

        async def listing(sym: str) -> tuple[str, list[Listed]]:
            async with sem:
                objs, _ = await list_keys(
                    session, f"{PREFIX}/monthly/klines/{sym}/1d/", listing_url=listing_url
                )
            return sym, [o for o in objs if o.key.endswith(".zip")]

        listed = dict(await asyncio.gather(*(listing(s) for s in symbols)))
        first_month = {
            s: min((stamp_of(o.key) for o in objs), default="") for s, objs in listed.items()
        }
        items = [o for objs in listed.values() for o in objs if stamp_of(o.key) in want]
        used = await fetch_listed(session, items, mirror, base_url, concurrency)
    qv: dict[str, dict[str, float]] = defaultdict(dict)
    first_day: dict[str, date] = {}
    for u in used:
        if u.result.path is None:
            log.warning("universe_file_failed", key=u.listed.key, status=u.result.status)
            continue
        sym = u.listed.key.split("/")[-3]
        t = read_csv_zip(u.result.path, "klines")
        if t.num_rows == 0:
            continue
        qv[sym][stamp_of(u.listed.key)] = float(pc.sum(t.column("quote_volume")).as_py() or 0.0)
        first_ms = int(pc.min(t.column("open_time")).as_py())
        first_ms = first_ms // 1000 if first_ms > 10**15 else first_ms  # µs archives
        d = date(1970, 1, 1) + timedelta(milliseconds=first_ms)
        first_day[sym] = min(first_day.get(sym, d), d)
    for sym, fm in first_month.items():  # listed before our window: history is long enough
        if fm and fm < min(want):
            first_day[sym] = date.fromisoformat(fm + "-01")
    rows: list[list[str]] = []
    for m in month_range(start, end):
        pm = prev_month(m)
        m_start = date.fromisoformat(m + "-01")
        ranked = sorted(
            (
                (qv[s][pm], s)
                for s in qv
                if pm in qv[s]
                and s in first_day
                and first_day[s] <= m_start - timedelta(days=min_history_days)
            ),
            reverse=True,
        )
        for rank, (vol, sym) in enumerate(ranked[:top_n], start=1):
            rows.append([m, str(rank), sym, f"{vol:.0f}"])
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(UNIVERSE_HEADER)
        w.writerows(rows)
    write_manifest(manifest, used)
    return rows


def read_universe(path: Path) -> list[tuple[str, int, str]]:
    with path.open(newline="") as fh:
        return [(r["month"], int(r["rank"]), r["symbol"]) for r in csv.DictReader(fh)]


def subset_universe(src: Path, dst: Path, top_n: int) -> int:
    """The top-``top_n`` of an existing point-in-time universe. Ranks come from the same
    previous-month volume ranking, so this equals rebuilding it with ``top_n``."""
    with src.open(newline="") as fh:
        rows = [r for r in csv.reader(fh)]
    if rows[0] != UNIVERSE_HEADER:
        raise ValueError(f"{src}: unexpected header {rows[0]}")
    keep = [r for r in rows[1:] if int(r[1]) <= top_n]
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(UNIVERSE_HEADER)
        w.writerows(keep)
    return len(keep)
