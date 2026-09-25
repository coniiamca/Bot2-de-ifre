"""Download the research dataset for a universe: hourly klines, premium index and funding for
every symbol that was ever in the universe (from ``pad_before`` months before its first
month to ``pad_after`` after its last), plus 5-minute metrics (open interest, trader ratios)
for a small core set. Files are verified (MD5 from the listing), converted to Parquet under
``<root>/lake/binance_vision_um/`` and recorded in a manifest.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import aiohttp

from quanta.archive.binance_vision import BASE_URL, LISTING_URL, PREFIX, Listed, convert, list_keys
from quanta.core.log import get_logger
from quanta.research.universe import Used, fetch_listed, month_range, stamp_of, write_manifest

log = get_logger(__name__)

MONTHLY = (("klines", "1h"), ("premiumIndexKlines", "1h"), ("fundingRate", ""))
MINUTE = (("klines", "1m"),)  # intraday research: 1-minute bars incl. taker buy volume
CORE_METRICS = ("BTCUSDT", "ETHUSDT")


def _shift(month: str, n: int) -> str:
    d = date.fromisoformat(month + "-01")
    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    return f"{y:04d}-{m + 1:02d}"


def symbol_windows(
    universe: list[tuple[str, int, str]], pad_before: int = 4, pad_after: int = 1
) -> dict[str, tuple[str, str]]:
    months: dict[str, list[str]] = defaultdict(list)
    for month, _, sym in universe:
        months[sym].append(month)
    return {
        s: (_shift(min(ms), -pad_before), _shift(max(ms), pad_after)) for s, ms in months.items()
    }


async def fetch_dataset(
    root: Path,
    universe: list[tuple[str, int, str]],
    manifest: Path,
    *,
    datasets: tuple[tuple[str, str], ...] = MONTHLY,
    pad_before: int = 4,
    pad_after: int = 1,
    metrics_symbols: tuple[str, ...] = CORE_METRICS,
    metrics_start: date = date(2020, 9, 1),
    metrics_end: date | None = None,
    concurrency: int = 16,
    base_url: str = BASE_URL,
    listing_url: str = LISTING_URL,
) -> list[Used]:
    mirror = root / "archive" / "binance_vision"
    windows = symbol_windows(universe, pad_before, pad_after)
    last_month = max(m for m, _, _ in universe)
    metrics_end = metrics_end or (
        date.fromisoformat(_shift(last_month, 1) + "-01") - timedelta(days=1)
    )
    jobs: list[tuple[Listed, str, str, str]] = []  # (listed, dataset, symbol, interval)
    timeout = aiohttp.ClientTimeout(total=600)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        sem = asyncio.Semaphore(concurrency)

        async def listing(prefix: str) -> list[Listed]:
            async with sem:
                objs, _ = await list_keys(session, prefix, listing_url=listing_url)
            return [o for o in objs if o.key.endswith(".zip")]

        specs: list[tuple[str, str, str, str, str]] = []  # prefix, dataset, sym, interval, window
        for sym, (lo, hi) in windows.items():
            for ds, iv in datasets:
                sub = f"{sym}/{iv}/" if iv else f"{sym}/"
                specs.append((f"{PREFIX}/monthly/{ds}/{sub}", ds, sym, iv, f"{lo}|{hi}"))
        for sym in metrics_symbols:
            lo, hi = metrics_start.isoformat(), metrics_end.isoformat()
            specs.append((f"{PREFIX}/daily/metrics/{sym}/", "metrics", sym, "", f"{lo}|{hi}"))
        listings = await asyncio.gather(*(listing(s[0]) for s in specs))
        for (_, ds, sym, iv, window), objs in zip(specs, listings, strict=True):
            lo, hi = window.split("|")
            for o in objs:
                st = stamp_of(o.key)
                if lo <= st[: len(lo)] <= hi and st:
                    jobs.append((o, ds, sym, iv))
        log.info("research_fetch_jobs", files=len(jobs), symbols=len(windows))
        used = await fetch_listed(session, [j[0] for j in jobs], mirror, base_url, concurrency)
    for (listed, ds, sym, iv), u in zip(jobs, used, strict=True):
        if u.result.path is None:
            log.warning("research_fetch_failed", key=listed.key, status=u.result.status)
            continue
        part = "month" if len(stamp_of(listed.key)) == 7 else "date"
        name = f"{ds}_{iv}" if iv else ds
        out = (
            root
            / "lake"
            / "binance_vision_um"
            / name
            / f"symbol={sym}"
            / f"{part}={stamp_of(listed.key)}"
        )
        if not (out / "part-0.parquet").exists():
            await asyncio.to_thread(
                convert, u.result, ds, sym, stamp_of(listed.key), root, iv or "1m"
            )
    write_manifest(manifest, used)
    return used


def check_months(universe: list[tuple[str, int, str]], start: str, end: str) -> list[str]:
    """Months of ``start``..``end`` that have no universe rows (data gaps to report)."""
    have = {m for m, _, _ in universe}
    return [m for m in month_range(start, end) if m not in have]
