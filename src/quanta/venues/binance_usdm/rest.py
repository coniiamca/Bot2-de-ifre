"""Public REST client for Binance USDⓈ-M (market data; signed endpoints arrive in Phase 4).

Every response is returned with its raw body and timing so the recorder can store exactly
what the exchange sent (plan §8.2).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import aiohttp
import msgspec

from quanta.core.clock import NS_PER_S, Clock
from quanta.core.errors import QuantaError
from quanta.venues.binance_usdm.ratelimit import WeightBudget


class BinanceRestError(QuantaError):
    def __init__(self, status: int, body: bytes, path: str) -> None:
        self.status = status
        self.body = body
        self.path = path
        self.code: int | None = None
        self.msg = ""
        try:
            parsed = msgspec.json.decode(body)
            if isinstance(parsed, dict):
                self.code = parsed.get("code")
                self.msg = str(parsed.get("msg", ""))
        except msgspec.DecodeError:
            self.msg = body[:200].decode("utf-8", "replace")
        super().__init__(f"{path}: HTTP {status} code={self.code} msg={self.msg}")


class RestrictedLocationError(BinanceRestError):
    """HTTP 451 — the exchange refuses service from this host's location (plan §0, §19)."""


class RateLimitedError(BinanceRestError):
    def __init__(self, status: int, body: bytes, path: str, retry_after_s: float) -> None:
        super().__init__(status, body, path)
        self.retry_after_s = retry_after_s


# Documented request weights for the public endpoints we use.
DEPTH_WEIGHTS = {5: 2, 10: 2, 20: 2, 50: 2, 100: 5, 500: 10, 1000: 20}
WEIGHTS: dict[str, int] = {
    "/fapi/v1/time": 1,
    "/fapi/v1/ping": 1,
    "/fapi/v1/exchangeInfo": 1,
    "/fapi/v1/openInterest": 1,
    "/fapi/v1/premiumIndex": 10,  # without symbol
    "/fapi/v1/fundingInfo": 1,
    "/fapi/v1/fundingRate": 1,  # shares the 500 / 5 min IP limit with fundingInfo
    "/fapi/v1/insuranceBalance": 1,
    "/futures/data/openInterestHist": 0,  # separate IP limit (1000 / 5 min) on /futures/data
    "/futures/data/topLongShortAccountRatio": 0,
    "/futures/data/topLongShortPositionRatio": 0,
    "/futures/data/globalLongShortAccountRatio": 0,
    "/futures/data/takerlongshortRatio": 0,
}

STATS_PATHS = (
    "/futures/data/openInterestHist",
    "/futures/data/topLongShortAccountRatio",
    "/futures/data/topLongShortPositionRatio",
    "/futures/data/globalLongShortAccountRatio",
    "/futures/data/takerlongshortRatio",
)


@dataclass(frozen=True, slots=True)
class RestResponse:
    path: str
    params: dict[str, Any]
    status: int
    body: bytes
    sent_ns: int
    recv_ns: int
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def latency_s(self) -> float:
        return (self.recv_ns - self.sent_ns) / NS_PER_S

    def json(self) -> Any:
        return msgspec.json.decode(self.body)


class BinanceUsdmRest:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        budget: WeightBudget,
        clock: Clock,
        timeout_s: float = 10.0,
    ) -> None:
        self._session = session
        self._base = base_url.rstrip("/")
        self._budget = budget
        self._clock = clock
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)

    async def get(
        self, path: str, params: dict[str, Any] | None = None, weight: int | None = None
    ) -> RestResponse:
        params = dict(params or {})
        w = WEIGHTS.get(path, 1) if weight is None else weight
        if w:
            await self._budget.acquire(w)
        url = f"{self._base}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        sent = self._clock.now_ns()
        headers: dict[str, str] = {}
        try:
            async with self._session.get(url, timeout=self._timeout) as resp:
                body = await resp.read()
                recv = self._clock.now_ns()
                headers = {k: v for k, v in resp.headers.items() if k.lower().startswith("x-mbx")}
                if "Retry-After" in resp.headers:
                    headers["Retry-After"] = resp.headers["Retry-After"]
                status = resp.status
        except (aiohttp.ClientError, TimeoutError):
            if w:
                self._budget.release(w, None)
            raise
        if w:
            self._budget.release(w, headers)
        result = RestResponse(path, params, status, body, sent, recv, headers)
        if status == 200:
            return result
        if status == 451:
            raise RestrictedLocationError(status, body, path)
        if status in (418, 429):
            retry_after = float(headers.get("Retry-After", "60") or 60)
            self._budget.block_for(retry_after)
            raise RateLimitedError(status, body, path, retry_after)
        raise BinanceRestError(status, body, path)

    # Convenience wrappers --------------------------------------------------------------
    async def server_time(self) -> RestResponse:
        return await self.get("/fapi/v1/time")

    async def exchange_info(self) -> RestResponse:
        return await self.get("/fapi/v1/exchangeInfo")

    async def depth(self, symbol: str, limit: int = 1000) -> RestResponse:
        if limit not in DEPTH_WEIGHTS:
            raise ValueError(f"unsupported depth limit {limit}")
        return await self.get(
            "/fapi/v1/depth", {"symbol": symbol, "limit": limit}, DEPTH_WEIGHTS[limit]
        )

    async def open_interest(self, symbol: str) -> RestResponse:
        return await self.get("/fapi/v1/openInterest", {"symbol": symbol})

    async def premium_index(self) -> RestResponse:
        return await self.get("/fapi/v1/premiumIndex")

    async def funding_info(self) -> RestResponse:
        return await self.get("/fapi/v1/fundingInfo")

    async def funding_rate(self, symbol: str, limit: int = 3) -> RestResponse:
        """Realized funding history (latest ``limit`` settlements)."""
        return await self.get("/fapi/v1/fundingRate", {"symbol": symbol, "limit": limit})

    async def insurance_balance(self) -> RestResponse:
        return await self.get("/fapi/v1/insuranceBalance")

    async def stats(
        self, path: str, symbol: str, period: str = "5m", limit: int = 3
    ) -> RestResponse:
        if path not in STATS_PATHS:
            raise ValueError(f"unknown stats path {path}")
        return await self.get(path, {"symbol": symbol, "period": period, "limit": limit})


async def measure_server_time(
    rest: BinanceUsdmRest, samples: int = 10, pause_s: float = 0.2
) -> list[tuple[float, float]]:
    """Returns (round-trip seconds, clock offset seconds = local_mid − server) per sample."""
    out: list[tuple[float, float]] = []
    for _ in range(samples):
        resp = await rest.server_time()
        server_ms = int(resp.json()["serverTime"])
        mid_ns = (resp.sent_ns + resp.recv_ns) // 2
        out.append((resp.latency_s, (mid_ns - server_ms * 1_000_000) / NS_PER_S))
        await asyncio.sleep(pause_s)
    return out
