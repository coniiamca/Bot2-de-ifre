"""A fake Binance USDⓈ-M exchange for integration tests.

Implements the parts of the protocol the recorder depends on:
* WS routes ``/public/stream`` and ``/market/stream`` with ``?streams=a/b`` combined format;
* depth diff events with U/u/pu semantics (ids jump because the counter is global across
  symbols, like on Binance), bookTicker, aggTrade (contiguous ids), markPrice, forceOrder;
* REST: time, exchangeInfo, depth snapshots (consistent with lastUpdateId), OI, premium
  index, funding info, insurance balance, /futures/data stats.

Fault injection: drop depth events (gap), close all sockets (disconnect), HTTP 451 mode,
slow snapshots. Ground truth (all generated trades/depth events) is kept for assertions.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import time
from dataclasses import dataclass, field
from typing import Any

from aiohttp import WSMsgType, web


def _ms() -> int:
    return int(time.time() * 1000)


@dataclass
class SymbolState:
    symbol: str
    bids: dict[int, int] = field(default_factory=dict)  # price in 0.1 units → qty in 0.001
    asks: dict[int, int] = field(default_factory=dict)
    last_u: int = 0
    agg_id: int = 5_000
    trade_id: int = 90_000
    trades: list[dict[str, Any]] = field(default_factory=list)
    depth_events: list[dict[str, Any]] = field(default_factory=list)


def _px(p: int) -> str:
    return f"{p // 10}.{p % 10}"


def _qty(q: int) -> str:
    return f"{q // 1000}.{q % 1000:03d}"


class FakeBinance:
    def __init__(self, symbols: list[str], tick_s: float = 0.02, seed: int = 7) -> None:
        self.rng = random.Random(seed)
        self.tick_s = tick_s
        self.global_id = 1_000_000
        self.state = {s: self._init_symbol(s) for s in symbols}
        self.conns: list[tuple[web.WebSocketResponse, set[str], asyncio.Queue[str]]] = []
        self.drop_depth: dict[str, int] = {}
        self.bad_price_once: set[str] = set()
        self.restricted = False
        self.snapshot_delay_s = 0.0
        self.rest_calls: list[str] = []
        self.ws_connects = 0
        self._task: asyncio.Task[None] | None = None
        self._runner: web.AppRunner | None = None
        self.port = 0
        app = web.Application()
        app.router.add_get("/public/stream", self._ws)
        app.router.add_get("/market/stream", self._ws)
        app.router.add_get("/fapi/v1/time", self._time)
        app.router.add_get("/fapi/v1/exchangeInfo", self._exchange_info)
        app.router.add_get("/fapi/v1/depth", self._depth)
        app.router.add_get("/fapi/v1/openInterest", self._open_interest)
        app.router.add_get("/fapi/v1/premiumIndex", self._premium_index)
        app.router.add_get("/fapi/v1/fundingInfo", self._funding_info)
        app.router.add_get("/fapi/v1/fundingRate", self._funding_rate)
        app.router.add_get("/fapi/v1/insuranceBalance", self._simple([]))
        app.router.add_get("/futures/data/{name}", self._stats)
        self.app = app

    def _init_symbol(self, s: str) -> SymbolState:
        st = SymbolState(s)
        for i in range(1, 30):
            st.bids[1000 - i] = self.rng.randint(1, 5000)
            st.asks[1000 + i] = self.rng.randint(1, 5000)
        self.global_id += 1
        st.last_u = self.global_id
        return st

    @property
    def rest_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    async def start(self) -> None:
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        self._task = asyncio.create_task(self._ticker())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self.close_all_ws()
        if self._runner:
            await self._runner.cleanup()

    async def close_all_ws(self) -> None:
        for ws, _, _ in list(self.conns):
            await ws.close()

    # -- market simulation -------------------------------------------------------------
    async def _ticker(self) -> None:
        while True:
            for st in self.state.values():
                self._step(st)
            await asyncio.sleep(self.tick_s)

    def _step(self, st: SymbolState) -> None:
        now = _ms()
        s = st.symbol.lower()
        bids: list[list[str]] = []
        asks: list[list[str]] = []
        for _ in range(self.rng.randint(1, 3)):
            is_bid = self.rng.random() < 0.5
            side = st.bids if is_bid else st.asks
            price = (1000 - self.rng.randint(1, 35)) if is_bid else (1000 + self.rng.randint(1, 35))
            qty = 0 if self.rng.random() < 0.3 else self.rng.randint(1, 5000)
            if qty == 0:
                side.pop(price, None)
            else:
                side[price] = qty
            (bids if is_bid else asks).append([_px(price), _qty(qty)])
        U = self.global_id + 1
        self.global_id += self.rng.randint(1, 4)  # ids jump (global counter)
        u = self.global_id
        if st.symbol in self.bad_price_once:
            self.bad_price_once.discard(st.symbol)
            bids.append(["99.95", "1.000"])  # finer than the 0.10 tick size (not applied)
        ev = {
            "e": "depthUpdate",
            "E": now,
            "T": now,
            "s": st.symbol,
            "U": U,
            "u": u,
            "pu": st.last_u,
            "b": bids,
            "a": asks,
        }
        st.last_u = u
        st.depth_events.append(ev)
        if self.drop_depth.get(st.symbol, 0) > 0:
            self.drop_depth[st.symbol] -= 1
        else:
            self._broadcast(f"{s}@depth@100ms", ev)
        st.agg_id += 1
        first = st.trade_id + 1
        st.trade_id += self.rng.randint(1, 3)
        trade = {
            "e": "aggTrade",
            "E": now,
            "s": st.symbol,
            "a": st.agg_id,
            "p": _px(1000 + self.rng.randint(-2, 2)),
            "q": _qty(self.rng.randint(1, 900)),
            "nq": "0.001",
            "f": first,
            "l": st.trade_id,
            "T": now,
            "m": self.rng.random() < 0.5,
        }
        st.trades.append(trade)
        self._broadcast(f"{s}@aggTrade", trade)
        bb, ba = max(st.bids), min(st.asks)
        self._broadcast(
            f"{s}@bookTicker",
            {
                "e": "bookTicker",
                "u": u,
                "E": now,
                "T": now,
                "s": st.symbol,
                "b": _px(bb),
                "B": _qty(st.bids[bb]),
                "a": _px(ba),
                "A": _qty(st.asks[ba]),
            },
        )
        self._broadcast(
            f"{s}@markPrice@1s",
            {
                "e": "markPriceUpdate",
                "E": now,
                "s": st.symbol,
                "p": "100.0",
                "i": "100.0",
                "P": "100.0",
                "r": "0.0001",
                "T": now + 3_600_000,
            },
        )
        if self.rng.random() < 0.05:
            self._broadcast(
                "!forceOrder@arr",
                {
                    "e": "forceOrder",
                    "E": now,
                    "st": 1,
                    "o": {
                        "s": st.symbol,
                        "S": "SELL",
                        "q": "0.010",
                        "p": "99.0",
                        "ap": "99.0",
                        "X": "FILLED",
                        "T": now,
                    },
                },
            )

    def _broadcast(self, stream: str, data: dict[str, Any]) -> None:
        text = json.dumps({"stream": stream, "data": data}, separators=(",", ":"))
        for _, streams, q in self.conns:
            if stream in streams:
                q.put_nowait(text)

    # -- handlers ------------------------------------------------------------------------
    async def _ws(self, request: web.Request) -> web.WebSocketResponse:
        if self.restricted:
            raise web.HTTPUnavailableForLegalReasons(link=None)
        streams = set(request.query.get("streams", "").split("/"))
        ws = web.WebSocketResponse(autoping=True)
        await ws.prepare(request)
        self.ws_connects += 1
        q: asyncio.Queue[str] = asyncio.Queue()
        entry = (ws, streams, q)
        self.conns.append(entry)

        async def sender() -> None:
            while True:
                text = await q.get()
                await ws.send_str(text)

        task = asyncio.create_task(sender())
        try:
            async for m in ws:
                if m.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            self.conns.remove(entry)
        return ws

    def _guard(self, request: web.Request) -> None:
        self.rest_calls.append(request.path)
        if self.restricted:
            raise web.HTTPUnavailableForLegalReasons(
                link=None, text='{"code":0,"msg":"Service unavailable from a restricted location"}'
            )

    async def _time(self, request: web.Request) -> web.Response:
        self._guard(request)
        return web.json_response({"serverTime": _ms()}, headers={"X-MBX-USED-WEIGHT-1M": "3"})

    async def _exchange_info(self, request: web.Request) -> web.Response:
        self._guard(request)
        symbols = [
            {
                "symbol": s,
                "status": "TRADING",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                ],
            }
            for s in self.state
        ]
        return web.json_response({"symbols": symbols})

    async def _depth(self, request: web.Request) -> web.Response:
        self._guard(request)
        st = self.state[request.query["symbol"]]
        limit = int(request.query.get("limit", "1000"))
        # capture state now; respond later (events keep flowing, like on the real exchange)
        body = {
            "lastUpdateId": st.last_u,
            "E": _ms(),
            "T": _ms(),
            "bids": [[_px(p), _qty(q)] for p, q in sorted(st.bids.items(), reverse=True)][:limit],
            "asks": [[_px(p), _qty(q)] for p, q in sorted(st.asks.items())][:limit],
        }
        if self.snapshot_delay_s:
            await asyncio.sleep(self.snapshot_delay_s)
        return web.json_response(body, headers={"X-MBX-USED-WEIGHT-1M": "20"})

    async def _open_interest(self, request: web.Request) -> web.Response:
        self._guard(request)
        return web.json_response(
            {"openInterest": "10.000", "symbol": request.query["symbol"], "time": _ms()}
        )

    async def _premium_index(self, request: web.Request) -> web.Response:
        self._guard(request)
        now = _ms()
        return web.json_response(
            [
                {
                    "symbol": s,
                    "markPrice": "100.0",
                    "indexPrice": "100.0",
                    "estimatedSettlePrice": "100.0",
                    "lastFundingRate": "0.0001",
                    "interestRate": "0.0001",
                    "nextFundingTime": now + 3_600_000,
                    "time": now,
                }
                for s in self.state
            ]
        )

    async def _funding_info(self, request: web.Request) -> web.Response:
        self._guard(request)
        return web.json_response(
            [
                {
                    "symbol": s,
                    "adjustedFundingRateCap": "0.02",
                    "adjustedFundingRateFloor": "-0.02",
                    "fundingIntervalHours": 8,
                    "disclaimer": False,
                }
                for s in self.state
            ]
        )

    async def _funding_rate(self, request: web.Request) -> web.Response:
        self._guard(request)
        t0 = _ms() // 28_800_000 * 28_800_000
        return web.json_response(
            [
                {
                    "symbol": request.query["symbol"],
                    "fundingRate": "0.00010000",
                    "fundingTime": t0 - i * 28_800_000,
                    "markPrice": "100.0",
                }
                for i in range(3)
            ]
        )

    async def _stats(self, request: web.Request) -> web.Response:
        self._guard(request)
        s, name = request.query["symbol"], request.match_info["name"]
        ts = _ms() // 300_000 * 300_000
        if name == "openInterestHist":
            row = {"sumOpenInterest": "10.0", "sumOpenInterestValue": "1000.0"}
        elif name == "takerlongshortRatio":
            row = {"buySellRatio": "1.1", "buyVol": "11.0", "sellVol": "10.0"}
        else:
            row = {"longShortRatio": "1.2", "longAccount": "0.55", "shortAccount": "0.45"}
        return web.json_response([{"symbol": s, **row, "timestamp": ts}])

    def _simple(self, payload: Any):  # type: ignore[no-untyped-def]
        async def handler(request: web.Request) -> web.Response:
            self._guard(request)
            return web.json_response(payload)

        return handler
