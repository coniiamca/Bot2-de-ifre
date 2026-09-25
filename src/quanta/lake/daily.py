"""Daily lake job and its scheduler (``quanta lake daily`` / ``quanta lake schedule``).

The scheduler replaces host cron in the default compose stack: it runs the job every day at
``hour:minute`` UTC and, on start, catches up yesterday if its quality report is missing
(e.g. the server was down at the scheduled time).

With check settings (from the recorder config) the daily verification of
:mod:`quanta.lake.checks` runs after the job, and again every ``checks_every_h`` hours: the
official trade archive is published with a lag, so pending days are retried.

Like the recorder's disk guard, the job never fills the data volume: a venue is skipped (and
reported as a problem) when free space is below ``min_free_bytes``, before or while it is
normalized.

A marker ``lake/_quality/date=….running`` exists while a day is processed. If the process dies
(e.g. killed for memory), the marker stays and the start-up catch-up does not retry that day,
so a failure cannot turn into a restart loop; ``quanta lake daily --date`` retries it by hand.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from quanta.core.log import get_logger
from quanta.lake.checks import CheckSettings, run_pending
from quanta.lake.normalize import VENUES, PartialDataError, normalize_day
from quanta.lake.quality import quality_report
from quanta.lake.table import DiskGuardError

log = get_logger(__name__)


def _free_bytes(path: Path) -> float:
    return float(shutil.disk_usage(path).free)


def run_daily(
    data_dir: Path,
    day: date,
    min_free_bytes: float = 0.0,
    disk_free: Callable[[Path], float] = _free_bytes,
) -> tuple[list[str], dict[str, Any]]:
    """Normalize every venue that has raw data for ``day`` and write the quality report.
    A venue is skipped while free space is below ``min_free_bytes``."""
    venues = [v for v in VENUES if (data_dir / "raw" / v).exists()]
    problems: list[str] = []
    done: list[str] = []
    marker = _marker(data_dir, day)
    for v in venues:
        free = disk_free(data_dir)
        if free < min_free_bytes:
            msg = (
                f"{v}: skipped, {free / 1e9:.1f} GB free < {min_free_bytes / 1e9:.1f} GB "
                "required (disk guard); re-run `quanta lake daily --date` after freeing space"
            )
            log.error("lake_daily_disk_guard", venue=v, free_gb=round(free / 1e9, 2))
            problems.append(msg)
            continue
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        try:
            normalize_day(data_dir, v, day, min_free_bytes=min_free_bytes, disk_free=disk_free)
        except PartialDataError as exc:
            problems.append(f"{v}: {exc}")
        except DiskGuardError as exc:
            log.error("lake_daily_disk_guard", venue=v, error=str(exc))
            problems.append(
                f"{v}: stopped, {exc}; re-run `quanta lake daily --date` after freeing space"
            )
            continue
        done.append(v)
    report = quality_report(data_dir, day, done) if done else {}
    marker.unlink(missing_ok=True)
    return problems, report


def next_run(now: datetime, hour: int = 0, minute: int = 20) -> datetime:
    """Next occurrence of hour:minute UTC strictly after ``now``."""
    now = now.astimezone(UTC)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(days=1)


def next_slot(
    now: datetime, hour: int = 0, minute: int = 20, every_h: int = 0
) -> tuple[datetime, bool]:
    """Next run strictly after ``now`` and whether it is the daily lake run (True) or a
    checks-only run, ``every_h`` hours apart from it (0 = no checks-only runs)."""
    main = next_run(now, hour, minute)
    if every_h <= 0:
        return main, True
    prev_main = main - timedelta(days=1)
    for k in range(1, 24 // every_h + 1):
        extra = prev_main + timedelta(hours=k * every_h)
        if now.astimezone(UTC) < extra < main:
            return extra, False
    return main, True


def _marker(data_dir: Path, day: date) -> Path:
    return data_dir / "lake" / "_quality" / f"date={day.isoformat()}.running"


def needs_catch_up(data_dir: Path, day: date) -> bool:
    has_raw = any((data_dir / "raw" / v).exists() for v in VENUES)
    report = data_dir / "lake" / "_quality" / f"date={day.isoformat()}.json"
    if _marker(data_dir, day).exists():
        log.error("lake_daily_crashed_before", date=day.isoformat(), note="not retried")
        return False
    return has_raw and not report.exists()


async def schedule(
    data_dir: Path,
    stop: asyncio.Event,
    hour: int = 0,
    minute: int = 20,
    min_free_bytes: float = 0.0,
    checks: CheckSettings | None = None,
    checks_every_h: int = 6,
) -> None:
    async def job(day: date) -> None:
        try:
            problems, report = await asyncio.to_thread(run_daily, data_dir, day, min_free_bytes)
        except Exception:
            log.exception("lake_daily_failed", date=day.isoformat())
            return
        log.info(
            "lake_daily_done",
            date=day.isoformat(),
            problems=problems,
            flags={v: q["flag"] for v, q in report.items()},
        )

    async def verify() -> None:
        if checks is None:
            return
        try:
            await asyncio.to_thread(run_pending, data_dir, checks)
        except Exception:
            log.exception("daily_checks_failed")

    yesterday = (datetime.now(UTC) - timedelta(days=1)).date()
    if needs_catch_up(data_dir, yesterday):
        await job(yesterday)
    await verify()
    every_h = checks_every_h if checks is not None else 0
    while not stop.is_set():
        now = datetime.now(UTC)
        run_at, is_main = next_slot(now, hour, minute, every_h)
        log.info("lake_daily_next_run", at=run_at.isoformat(), lake=is_main)
        try:
            await asyncio.wait_for(stop.wait(), timeout=(run_at - now).total_seconds())
            return
        except TimeoutError:
            pass
        if is_main:
            await job((run_at - timedelta(days=1)).date())
        await verify()
