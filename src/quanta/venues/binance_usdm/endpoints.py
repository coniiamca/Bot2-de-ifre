"""Binance USDⓈ-M Futures endpoints and WebSocket route mapping.

Since 2026-04-23 market streams are served from three routes (old URLs are retired):

* ``/public``  — high-frequency book data: ``@depth*``, ``@bookTicker``, ``!bookTicker``
* ``/market``  — ``@aggTrade``, ``@markPrice``, ``@kline_*``, ``@forceOrder``, tickers, …
* ``/private`` — user data (listenKey)

Source: developers.binance.com … /websocket-market-streams/Important-WebSocket-Change-Notice
Consequence: depth and trades of the same symbol arrive on *different* connections with no
shared ordering; downstream code orders by our arrival timestamp (ADR-004).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Route(StrEnum):
    PUBLIC = "public"
    MARKET = "market"
    PRIVATE = "private"


@dataclass(frozen=True, slots=True)
class Environment:
    name: str
    rest_url: str
    ws_url: str
    ws_api_url: str


PRODUCTION = Environment(
    name="production",
    rest_url="https://fapi.binance.com",
    ws_url="wss://fstream.binance.com",
    ws_api_url="wss://ws-fapi.binance.com/ws-fapi/v1",
)

# Official docs are inconsistent (general-info lists demo-fapi, WS API page still lists
# testnet.binancefuture.com); verified against the live demo environment in Phase 4.
DEMO = Environment(
    name="demo",
    rest_url="https://demo-fapi.binance.com",
    ws_url="wss://demo-fstream.binance.com",
    ws_api_url="wss://testnet.binancefuture.com/ws-fapi/v1",
)

ENVIRONMENTS = {e.name: e for e in (PRODUCTION, DEMO)}


def route_for_stream(stream: str) -> Route:
    """Map a stream name (e.g. ``btcusdt@depth@100ms``) to its WebSocket route."""
    if stream == "!bookTicker" or stream.endswith("@bookTicker"):
        return Route.PUBLIC
    name = stream.split("@", 1)[1] if "@" in stream else stream
    if name.startswith(("depth", "rpiDepth")):
        return Route.PUBLIC
    return Route.MARKET


def combined_stream_url(ws_url: str, route: Route, streams: list[str]) -> str:
    if not streams:
        raise ValueError("at least one stream is required")
    wrong = [s for s in streams if route_for_stream(s) is not route]
    if wrong:
        raise ValueError(f"streams {wrong} do not belong to route /{route}")
    return f"{ws_url.rstrip('/')}/{route.value}/stream?streams={'/'.join(streams)}"


# Stream name builders (symbols are lower-case in stream names).
def depth_stream(symbol: str, speed_ms: int = 100) -> str:
    if speed_ms not in (100, 250, 500):
        raise ValueError("depth speed must be 100, 250 or 500 ms")
    suffix = "" if speed_ms == 250 else f"@{speed_ms}ms"
    return f"{symbol.lower()}@depth{suffix}"


def book_ticker_stream(symbol: str) -> str:
    return f"{symbol.lower()}@bookTicker"


def agg_trade_stream(symbol: str) -> str:
    return f"{symbol.lower()}@aggTrade"


def mark_price_stream(symbol: str, one_second: bool = True) -> str:
    return f"{symbol.lower()}@markPrice{'@1s' if one_second else ''}"


def kline_stream(symbol: str, interval: str = "1m") -> str:
    return f"{symbol.lower()}@kline_{interval}"


ALL_FORCE_ORDERS = "!forceOrder@arr"
