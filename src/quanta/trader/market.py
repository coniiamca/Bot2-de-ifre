"""Live best bid/ask and mark price per symbol (``/public`` bookTicker, ``/market`` markPrice),
with the time each was last updated — the stale-data protection reads these ages."""

from __future__ import annotations

import asyncio
import contextlib

import aiohttp
import msgspec

from quanta.core.clock import Clock
from quanta.net.ws import ManagedWebSocket, WsSettings
from quanta.risk.limits import MarketView
from quanta.venues.binance_usdm.endpoints import (
    Route,
    book_ticker_stream,
    combined_stream_url,
    mark_price_stream,
)


class MarketFeed:
    def __init__(
        self, ws_url: str, symbols: list[str], session: aiohttp.ClientSession, clock: Clock
    ) -> None:
        self.clock = clock
        self._book: dict[str, tuple[float, float, int]] = {}
        self._mark: dict[str, tuple[float, int]] = {}
        settings = WsSettings(idle_timeout_s=30.0)
        self._ws = [
            ManagedWebSocket(
                "trader-book",
                combined_stream_url(ws_url, Route.PUBLIC, [book_ticker_stream(s) for s in symbols]),
                session,
                clock,
                self._frame,
                settings=settings,
            ),
            ManagedWebSocket(
                "trader-mark",
                combined_stream_url(ws_url, Route.MARKET, [mark_price_stream(s) for s in symbols]),
                session,
                clock,
                self._frame,
                settings=settings,
            ),
        ]

    def _frame(self, conn_id: str, seq: int, recv_ns: int, text: str) -> None:
        msg = msgspec.json.decode(text)
        data = msg.get("data", msg) if isinstance(msg, dict) else None
        if not isinstance(data, dict):
            return
        kind = data.get("e")
        if kind == "bookTicker":
            self._book[data["s"]] = (float(data["b"]), float(data["a"]), recv_ns)
        elif kind == "markPriceUpdate":
            self._mark[data["s"]] = (float(data["p"]), recv_ns)

    def view(self, symbol: str) -> MarketView:
        now = self.clock.now_ns()
        v = MarketView()
        if symbol in self._book:
            v.bid, v.ask, t = self._book[symbol]
            v.book_age_s = (now - t) / 1e9
        if symbol in self._mark:
            v.mark, t = self._mark[symbol]
            v.mark_age_s = (now - t) / 1e9
        return v

    @property
    def connected(self) -> bool:
        return all(w.connected for w in self._ws)

    async def run(self, stop: asyncio.Event) -> None:
        tasks = [asyncio.create_task(w.run()) for w in self._ws]
        await stop.wait()
        for w in self._ws:
            w.stop()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(t, 5.0)
