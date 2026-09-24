"""REST request-weight budgeting for Binance USDⓈ-M.

The exchange's view (``X-MBX-USED-WEIGHT-1M`` response header) is authoritative; locally we
add the weight of requests in flight and keep usage under a configured fraction of the limit
(default 2400/min per IP). 429 → back off for Retry-After; 418 (IP ban) → stop all requests
for Retry-After and alert. We must never trigger 418: bans escalate from 2 minutes to 3 days.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from quanta.core.clock import NS_PER_S, Clock

MINUTE_NS = 60 * NS_PER_S


class WeightBudget:
    def __init__(self, clock: Clock, limit_per_minute: int = 2400, fraction: float = 0.5) -> None:
        if not 0 < fraction <= 1:
            raise ValueError("fraction must be in (0, 1]")
        self._clock = clock
        self.limit = int(limit_per_minute * fraction)
        self._window_start = self._window(clock.now_ns())
        self._used = 0  # best estimate for current window (header or local count)
        self._in_flight = 0
        self._blocked_until_ns = 0
        self._lock = asyncio.Lock()

    @staticmethod
    def _window(ts_ns: int) -> int:
        return ts_ns - ts_ns % MINUTE_NS

    @property
    def used(self) -> int:
        self._roll(self._clock.now_ns())
        return self._used

    @property
    def blocked_until_ns(self) -> int:
        return self._blocked_until_ns

    def _roll(self, now_ns: int) -> None:
        w = self._window(now_ns)
        if w != self._window_start:
            self._window_start = w
            self._used = 0

    async def acquire(self, weight: int) -> None:
        if weight > self.limit:
            raise ValueError(f"request weight {weight} exceeds budget {self.limit}")
        async with self._lock:
            while True:
                now = self._clock.now_ns()
                self._roll(now)
                if now < self._blocked_until_ns:
                    await asyncio.sleep((self._blocked_until_ns - now) / NS_PER_S)
                    continue
                if self._used + self._in_flight + weight <= self.limit:
                    self._in_flight += weight
                    return
                next_window = self._window_start + MINUTE_NS
                await asyncio.sleep(max(0.05, (next_window - now) / NS_PER_S))

    def release(self, weight: int, headers: Mapping[str, str] | None) -> None:
        now = self._clock.now_ns()
        self._roll(now)
        self._in_flight = max(0, self._in_flight - weight)
        header_used = _header_int(headers, "X-MBX-USED-WEIGHT-1M")
        if header_used is not None:
            # Responses may arrive out of order; the highest value seen in this window wins.
            self._used = max(self._used, header_used)
        else:
            self._used += weight

    def block_for(self, seconds: float) -> None:
        until = self._clock.now_ns() + int(seconds * NS_PER_S)
        self._blocked_until_ns = max(self._blocked_until_ns, until)


def _header_int(headers: Mapping[str, str] | None, name: str) -> int | None:
    if not headers:
        return None
    for k, v in headers.items():
        if k.lower() == name.lower():
            try:
                return int(v)
            except ValueError:
                return None
    return None
