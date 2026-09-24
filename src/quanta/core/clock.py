"""Clocks.

Every timestamp in quanta is an integer number of nanoseconds since the Unix epoch (UTC).
Two clocks exist and are the only time source components may use:

* ``LiveClock`` — wall clock (disciplined by chrony on production hosts) plus a monotonic
  clock for measuring intervals.
* ``SimClock`` — deterministic clock advanced by the event loop in backtest/replay, so the
  same code path produces identical results for identical inputs (ADR-004).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Protocol

NS_PER_US = 1_000
NS_PER_MS = 1_000_000
NS_PER_S = 1_000_000_000
NS_PER_HOUR = 3_600 * NS_PER_S
NS_PER_DAY = 24 * NS_PER_HOUR


class Clock(Protocol):
    def now_ns(self) -> int:
        """Wall-clock time, ns since epoch."""
        ...

    def monotonic_ns(self) -> int:
        """Monotonic time for measuring intervals (not comparable across processes)."""
        ...


class LiveClock:
    __slots__ = ()

    def now_ns(self) -> int:
        return time.time_ns()

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()


class SimClock:
    """Deterministic clock; time only moves when the event loop advances it."""

    __slots__ = ("_now",)

    def __init__(self, start_ns: int = 0) -> None:
        self._now = start_ns

    def now_ns(self) -> int:
        return self._now

    def monotonic_ns(self) -> int:
        return self._now

    def advance_to(self, ts_ns: int) -> None:
        if ts_ns < self._now:
            raise ValueError(f"SimClock cannot move backwards: {ts_ns} < {self._now}")
        self._now = ts_ns


def ms_to_ns(ms: int) -> int:
    return ms * NS_PER_MS


def ns_to_datetime(ts_ns: int) -> datetime:
    return datetime.fromtimestamp(ts_ns / NS_PER_S, tz=UTC)


def ns_to_iso(ts_ns: int) -> str:
    return ns_to_datetime(ts_ns).isoformat(timespec="microseconds").replace("+00:00", "Z")


def floor_ns(ts_ns: int, period_ns: int) -> int:
    return ts_ns - (ts_ns % period_ns)
