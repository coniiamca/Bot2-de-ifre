"""Region/access and latency check for a candidate host (plan §0, §5, Phase 0).

Answers, from the machine it runs on:
* Does Binance serve this location? (HTTP 451 = no; the host must move.)
* REST round-trip distribution and local clock offset vs. exchange server time.
* WebSocket event latency (receive time − exchange event time) on /market and /public.
"""

from __future__ import annotations

import asyncio
import statistics
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from quanta.core.clock import NS_PER_MS, LiveClock
from quanta.venues.binance_usdm import endpoints as ep
from quanta.venues.binance_usdm import messages as msg
from quanta.venues.binance_usdm.ratelimit import WeightBudget
from quanta.venues.binance_usdm.rest import (
    BinanceRestError,
    BinanceUsdmRest,
    RestrictedLocationError,
)


@dataclass(slots=True)
class AccessReport:
    environment: str
    rest_url: str
    reachable: bool = False
    restricted: bool = False
    error: str | None = None
    rtt_ms: dict[str, float] = field(default_factory=dict)
    clock_offset_ms: float | None = None
    ws_latency_ms: dict[str, dict[str, float]] = field(default_factory=dict)
    ws_messages: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.reachable and not self.restricted and self.error is None


def _pct(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    qs = statistics.quantiles(values, n=100) if len(values) >= 2 else values * 99
    return {
        "p50": round(qs[49], 2),
        "p90": round(qs[89], 2),
        "max": round(max(values), 2),
        "n": float(len(values)),
    }


async def _ws_latency(
    session: aiohttp.ClientSession, url: str, seconds: float
) -> tuple[list[float], int]:
    clock = LiveClock()
    lat: list[float] = []
    count = 0
    async with session.ws_connect(url, heartbeat=20) as ws:
        loop = asyncio.get_running_loop()
        end = loop.time() + seconds
        while (remaining := end - loop.time()) > 0:
            try:
                m = await ws.receive(timeout=remaining)
            except TimeoutError:
                break
            if m.type is not aiohttp.WSMsgType.TEXT:
                break
            recv = clock.now_ns()
            count += 1
            env = msg.decode_envelope(m.data)
            e_ms = msg.decode_event_time(env.data)
            if e_ms:
                lat.append((recv - e_ms * NS_PER_MS) / NS_PER_MS)
    return lat, count


async def check_access(
    environment: str = "production",
    samples: int = 10,
    ws_seconds: float = 10.0,
    symbol: str = "BTCUSDT",
    rest_url: str | None = None,
    ws_url: str | None = None,
) -> AccessReport:
    env = ep.ENVIRONMENTS[environment]
    rest_base = rest_url or env.rest_url
    ws_base = ws_url or env.ws_url
    rep = AccessReport(environment, rest_base)
    clock = LiveClock()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        rest = BinanceUsdmRest(session, rest_base, WeightBudget(clock, fraction=0.2), clock)
        rtts: list[float] = []
        offsets: list[float] = []
        try:
            for _ in range(samples):
                r = await rest.server_time()
                server_ms = int(r.json()["serverTime"])
                rtts.append((r.recv_ns - r.sent_ns) / NS_PER_MS)
                offsets.append(((r.sent_ns + r.recv_ns) // 2 - server_ms * NS_PER_MS) / NS_PER_MS)
                await asyncio.sleep(0.2)
            rep.reachable = True
        except RestrictedLocationError as exc:
            rep.restricted = True
            rep.error = f"HTTP 451 restricted location: {exc.msg}"
            return rep
        except (BinanceRestError, aiohttp.ClientError, TimeoutError) as exc:
            rep.error = repr(exc)
            return rep
        rep.rtt_ms = _pct(rtts)
        rep.clock_offset_ms = round(statistics.median(offsets), 2)
        tests: dict[str, tuple[ep.Route, list[str]]] = {
            "market": (
                ep.Route.MARKET,
                [ep.agg_trade_stream(symbol), ep.mark_price_stream(symbol)],
            ),
            "public": (ep.Route.PUBLIC, [ep.book_ticker_stream(symbol)]),
        }
        for name, (route, streams) in tests.items():
            try:
                lat, count = await _ws_latency(
                    session, ep.combined_stream_url(ws_base, route, streams), ws_seconds
                )
            except (aiohttp.ClientError, TimeoutError) as exc:
                rep.error = f"ws {name}: {exc!r}"
                continue
            rep.ws_latency_ms[name] = _pct(lat)
            rep.ws_messages[name] = count
    return rep


def report_dict(rep: AccessReport) -> dict[str, Any]:
    return {
        "ok": rep.ok,
        "environment": rep.environment,
        "rest_url": rep.rest_url,
        "reachable": rep.reachable,
        "restricted": rep.restricted,
        "error": rep.error,
        "rest_rtt_ms": rep.rtt_ms,
        "clock_offset_ms": rep.clock_offset_ms,
        "ws_event_latency_ms": rep.ws_latency_ms,
        "ws_messages": rep.ws_messages,
        "note": "clock_offset = local − server (RTT midpoint); keep |offset| < 250 ms",
    }
