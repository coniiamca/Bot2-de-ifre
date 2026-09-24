"""Daily lake job and its scheduler (``quanta lake daily`` / ``quanta lake schedule``).

The scheduler replaces host cron in the default compose stack: it runs the job every day at
``hour:minute`` UTC and, on start, catches up yesterday if its quality report is missing
(e.g. the server was down at the scheduled time).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from quanta.core.log import get_logger
from quanta.lake.normalize import VENUES, PartialDataError, normalize_day
from quanta.lake.quality import quality_report

log = get_logger(__name__)


def run_daily(data_dir: Path, day: date) -> tuple[list[str], dict[str, Any]]:
    """Normalize every venue that has raw data for ``day`` and write the quality report."""
    venues = [v for v in VENUES if (data_dir / "raw" / v).exists()]
    problems: list[str] = []
    for v in venues:
        try:
            normalize_day(data_dir, v, day)
        except PartialDataError as exc:
            problems.append(f"{v}: {exc}")
    report = quality_report(data_dir, day, venues) if venues else {}
    return problems, report


def next_run(now: datetime, hour: int = 0, minute: int = 20) -> datetime:
    """Next occurrence of hour:minute UTC strictly after ``now``."""
    now = now.astimezone(UTC)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(days=1)


def needs_catch_up(data_dir: Path, day: date) -> bool:
    has_raw = any((data_dir / "raw" / v).exists() for v in VENUES)
    report = data_dir / "lake" / "_quality" / f"date={day.isoformat()}.json"
    return has_raw and not report.exists()


async def schedule(data_dir: Path, stop: asyncio.Event, hour: int = 0, minute: int = 20) -> None:
    async def job(day: date) -> None:
        try:
            problems, report = await asyncio.to_thread(run_daily, data_dir, day)
        except Exception:
            log.exception("lake_daily_failed", date=day.isoformat())
            return
        log.info(
            "lake_daily_done",
            date=day.isoformat(),
            problems=problems,
            flags={v: q["flag"] for v, q in report.items()},
        )

    yesterday = (datetime.now(UTC) - timedelta(days=1)).date()
    if needs_catch_up(data_dir, yesterday):
        await job(yesterday)
    while not stop.is_set():
        now = datetime.now(UTC)
        run_at = next_run(now, hour, minute)
        log.info("lake_daily_next_run", at=run_at.isoformat())
        try:
            await asyncio.wait_for(stop.wait(), timeout=(run_at - now).total_seconds())
            return
        except TimeoutError:
            pass
        await job((run_at - timedelta(days=1)).date())
