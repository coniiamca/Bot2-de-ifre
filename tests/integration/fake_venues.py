"""Fake Bybit v5 (public linear) and Deribit (JSON-RPC) exchanges for integration tests.

Each fake keeps ground truth (book state, liquidations sent) so tests can assert that the
recorder's reconstruction is exact and its liquidation counts are complete.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from aiohttp import WSMsgType, web


def _ms() -> int:
    return int(time.time() * 1000)


OnSent = Callable[[], None] | None


@dataclass
class Conn:
    ws: web.WebSocketResponse
    topics: set[str] = field(default_factory=set)
    queue: asyncio.Queue[tuple[str, OnSent]] = field(default_factory=asyncio.Queue)


class _Server:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.app = web.Application()
        self.conns: list[Conn] = []
        self.port = 0
        self.paused = False
        self.ws_connects = 0
        self.geo_blocked = False  # answer REST and WS handshakes with HTTP 403
        self.ws_blocked = False  # … WS handshakes only
        self._runner: web.AppRunner | None = None
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        self._tasks.append(asyncio.create_task(self._ticker()))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
        await self.close_all_ws()
        if self._runner:
            await self._runner.cleanup()

    async def close_all_ws(self) -> None:
        for c in list(self.conns):
            await c.ws.close()

    async def _ticker(self) -> None:
        while True:
            if not self.paused:
                self.step()
            await asyncio.sleep(0.02)

    def step(self) -> None:
        raise NotImplementedError

    async def _on_text(self, conn: Conn, msg: dict[str, Any]) -> None:
        raise NotImplementedError

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        self.guard(ws=True)
        return await self.serve_ws(request, self._on_text)

    def guard(self, ws: bool = False) -> None:
        if self.geo_blocked or (ws and self.ws_blocked):
            raise web.HTTPForbidden(text="The service is not available in your region")

    liquidations_sent: dict[str, int]

    def _count_liquidations(self, key: str, n: int) -> OnSent:
        def count() -> None:
            self.liquidations_sent[key] += n

        return count if n else None

    def send(self, conn: Conn, obj: dict[str, Any], on_sent: OnSent = None) -> None:
        conn.queue.put_nowait((json.dumps(obj, separators=(",", ":")), on_sent))

    def broadcast(self, topic: str, obj: dict[str, Any], on_sent: OnSent = None) -> int:
        """Queues ``obj`` for every subscribed connection; ``on_sent`` runs once per frame
        actually written to a socket (a frame still queued when a connection is closed was
        never sent, so ground-truth counters must not count it)."""
        n = 0
        for c in self.conns:
            if topic in c.topics:
                self.send(c, obj, on_sent)
                n += 1
        return n

    async def serve_ws(self, request: web.Request, on_text: Any) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(autoping=True)
        await ws.prepare(request)
        self.ws_connects += 1
        conn = Conn(ws)
        self.conns.append(conn)

        async def sender() -> None:
            while True:
                text, on_sent = await conn.queue.get()
                await ws.send_str(text)
                if on_sent is not None:
                    on_sent()

        task = asyncio.create_task(sender())
        try:
            async for m in ws:
                if m.type is WSMsgType.TEXT:
                    await on_text(conn, json.loads(m.data))
                elif m.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            self.conns.remove(conn)
        return ws


def _book_init(rng: random.Random) -> tuple[dict[int, int], dict[int, int]]:
    bids = {1000 - i: rng.randint(1, 50) for i in range(1, 25)}
    asks = {1000 + i: rng.randint(1, 50) for i in range(1, 25)}
    return bids, asks


def _mutate(
    rng: random.Random, bids: dict[int, int], asks: dict[int, int]
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    db: list[tuple[int, int]] = []
    da: list[tuple[int, int]] = []
    for _ in range(rng.randint(1, 3)):
        is_bid = rng.random() < 0.5
        side, out = (bids, db) if is_bid else (asks, da)
        price = 1000 - rng.randint(1, 30) if is_bid else 1000 + rng.randint(1, 30)
        qty = 0 if rng.random() < 0.3 else rng.randint(1, 50)
        if qty:
            side[price] = qty
        else:
            side.pop(price, None)
        out.append((price, qty))
    return db, da


# ---------------------------------------------------------------------------------------
class FakeBybit(_Server):
    """``/v5/public/linear`` + ``/v5/market/instruments-info``. Prices in 0.1, qty in 0.001."""

    def __init__(self, symbols: list[str], seed: int = 11) -> None:
        super().__init__(seed)
        self.symbols = symbols
        self.books = {s: _book_init(self.rng) for s in symbols}
        self.u = dict.fromkeys(symbols, 1000)
        self.liquidations_sent = dict.fromkeys(symbols, 0)
        self.pings = 0
        self.bad_price_once: set[str] = set()
        self.app.router.add_get("/v5/public/linear", self._ws_handler)
        self.app.router.add_get("/v5/market/instruments-info", self._instruments)
        self.app.router.add_get("/v5/market/time", self._time)

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/v5/public/linear"

    @property
    def rest_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @staticmethod
    def _px(p: int) -> str:
        return f"{p // 10}.{p % 10}"

    @staticmethod
    def _q(q: int) -> str:
        return f"{q / 1000:.3f}"

    def _levels(self, levels: list[tuple[int, int]] | dict[int, int]) -> list[list[str]]:
        items = levels.items() if isinstance(levels, dict) else levels
        return [[self._px(p), self._q(q)] for p, q in items]

    def _snapshot(self, s: str) -> dict[str, Any]:
        bids, asks = self.books[s]
        return {
            "topic": f"orderbook.50.{s}",
            "type": "snapshot",
            "ts": _ms(),
            "data": {
                "s": s,
                "b": self._levels(dict(sorted(bids.items(), reverse=True))),
                "a": self._levels(dict(sorted(asks.items()))),
                "u": self.u[s],
                "seq": self.u[s] * 7,
            },
            "cts": _ms(),
        }

    async def _on_text(self, conn: Conn, msg: dict[str, Any]) -> None:
        if msg.get("op") == "ping":
            self.pings += 1
            self.send(conn, {"success": True, "ret_msg": "pong", "conn_id": "x", "op": "ping"})
        elif msg.get("op") == "subscribe":
            conn.topics.update(msg["args"])
            self.send(
                conn,
                {
                    "success": True,
                    "ret_msg": "",
                    "conn_id": "x",
                    "req_id": msg.get("req_id", ""),
                    "op": "subscribe",
                },
            )
            for t in msg["args"]:
                if t.startswith("orderbook."):
                    self.send(conn, self._snapshot(t.rsplit(".", 1)[-1]))

    def restart(self, s: str) -> None:
        """Service restart: u resets to 1 and a snapshot is pushed."""
        self.u[s] = 1
        self.broadcast(f"orderbook.50.{s}", self._snapshot(s))

    def step(self) -> None:
        for s in self.symbols:
            bids, asks = self.books[s]
            db, da = _mutate(self.rng, bids, asks)
            self.u[s] += 1
            b = self._levels(db)
            if s in self.bad_price_once:
                self.bad_price_once.discard(s)
                b.append(["99.95", "0.001"])  # finer than tick (never applied to truth)
            self.broadcast(
                f"orderbook.50.{s}",
                {
                    "topic": f"orderbook.50.{s}",
                    "type": "delta",
                    "ts": _ms(),
                    "data": {
                        "s": s,
                        "b": b,
                        "a": self._levels(da),
                        "u": self.u[s],
                        "seq": self.u[s] * 7,
                    },
                    "cts": _ms(),
                },
            )
            self.broadcast(
                f"publicTrade.{s}",
                {
                    "topic": f"publicTrade.{s}",
                    "type": "snapshot",
                    "ts": _ms(),
                    "data": [
                        {
                            "T": _ms(),
                            "s": s,
                            "S": "Buy",
                            "v": "0.010",
                            "p": "100.0",
                            "L": "PlusTick",
                            "i": f"t-{self.u[s]}",
                            "BT": False,
                        }
                    ],
                },
            )
            self.broadcast(
                f"tickers.{s}",
                {
                    "topic": f"tickers.{s}",
                    "type": "delta",
                    "ts": _ms(),
                    "cs": self.u[s],
                    "data": {"symbol": s, "markPrice": "100.0", "fundingRate": "0.0001"},
                },
            )
            if self.rng.random() < 0.2:
                self.broadcast(
                    f"allLiquidation.{s}",
                    {
                        "topic": f"allLiquidation.{s}",
                        "type": "snapshot",
                        "ts": _ms(),
                        "data": [{"T": _ms(), "s": s, "S": "Sell", "v": "0.003", "p": "99.0"}],
                    },
                    on_sent=self._count_liquidations(s, 1),
                )

    async def _time(self, request: web.Request) -> web.Response:
        self.guard()
        ns = time.time_ns()
        return web.json_response(
            {
                "retCode": 0,
                "retMsg": "OK",
                "result": {"timeSecond": str(ns // 10**9), "timeNano": str(ns)},
                "time": ns // 10**6,
            }
        )

    async def _instruments(self, request: web.Request) -> web.Response:
        s = request.query["symbol"]
        return web.json_response(
            {
                "retCode": 0,
                "result": {
                    "category": "linear",
                    "list": [
                        {
                            "symbol": s,
                            "priceFilter": {"tickSize": "0.10"},
                            "lotSizeFilter": {"qtyStep": "0.001"},
                        }
                    ],
                },
            }
        )


# ---------------------------------------------------------------------------------------
class FakeDeribit(_Server):
    """``/ws/api/v2`` JSON-RPC + ``/api/v2/public/get_instrument``. Tick 0.5, amount step 10."""

    def __init__(
        self, instruments: list[str], seed: int = 13, heartbeat_period_s: float = 0.3
    ) -> None:
        super().__init__(seed)
        self.instruments = instruments
        self.books = {i: _book_init(self.rng) for i in instruments}
        self.change_id = dict.fromkeys(instruments, 5000)
        self.trade_seq = dict.fromkeys(instruments, 100)
        self.liquidations_sent = dict.fromkeys(instruments, 0)
        self.drop_change: set[str] = set()
        self.heartbeat_period_s = heartbeat_period_s
        self.test_requests = 0
        self.test_replies = 0
        self.heartbeat_kills = 0
        self.unsubscribes = 0
        self.app.router.add_get("/ws/api/v2", self._ws_handler)
        self.app.router.add_get("/api/v2/public/get_instrument", self._get_instrument)
        self.app.router.add_get("/api/v2/public/get_time", self._get_time)

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws/api/v2"

    @property
    def rest_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/api/v2"

    @staticmethod
    def _price(p: int) -> float:
        return p / 2  # tick 0.5

    def _note(self, channel: str, data: Any) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "method": "subscription",
            "params": {"channel": channel, "data": data},
        }

    def _snapshot(self, i: str) -> dict[str, Any]:
        bids, asks = self.books[i]
        return self._note(
            f"book.{i}.100ms",
            {
                "type": "snapshot",
                "timestamp": _ms(),
                "instrument_name": i,
                "change_id": self.change_id[i],
                "bids": [
                    ["new", self._price(p), q * 10] for p, q in sorted(bids.items(), reverse=True)
                ],
                "asks": [["new", self._price(p), q * 10] for p, q in sorted(asks.items())],
            },
        )

    async def _on_text(self, conn: Conn, msg: dict[str, Any]) -> None:
        method, params, mid = msg.get("method"), msg.get("params", {}), msg.get("id")
        if method == "public/set_heartbeat":
            self.send(conn, {"jsonrpc": "2.0", "id": mid, "result": "ok"})
            self._tasks.append(asyncio.create_task(self._heartbeats(conn)))
        elif method == "public/test":
            self.test_replies += 1
            conn.topics.add("__test_ok__")
            self.send(conn, {"jsonrpc": "2.0", "id": mid, "result": {"version": "fake"}})
        elif method == "public/subscribe":
            conn.topics.update(params["channels"])
            self.send(conn, {"jsonrpc": "2.0", "id": mid, "result": params["channels"]})
            for ch in params["channels"]:
                if ch.startswith("book."):
                    self.send(conn, self._snapshot(ch.split(".")[1]))
        elif method == "public/unsubscribe":
            self.unsubscribes += 1
            conn.topics.difference_update(params["channels"])
            self.send(conn, {"jsonrpc": "2.0", "id": mid, "result": params["channels"]})

    async def _heartbeats(self, conn: Conn) -> None:
        """Send test_request; close the connection if the client did not answer in time."""
        with contextlib.suppress(Exception):
            while not conn.ws.closed:
                conn.topics.discard("__test_ok__")
                self.test_requests += 1
                self.send(
                    conn,
                    {"jsonrpc": "2.0", "method": "heartbeat", "params": {"type": "test_request"}},
                )
                await asyncio.sleep(self.heartbeat_period_s)
                if "__test_ok__" not in conn.topics:
                    self.heartbeat_kills += 1
                    await conn.ws.close()
                    return

    def step(self) -> None:
        for i in self.instruments:
            bids, asks = self.books[i]
            db, da = _mutate(self.rng, bids, asks)
            prev = self.change_id[i]
            self.change_id[i] += self.rng.randint(1, 3)

            def lv(items: list[tuple[int, int]]) -> list[list[Any]]:
                return [
                    ["delete" if q == 0 else "change", self._price(p), q * 10] for p, q in items
                ]

            note = self._note(
                f"book.{i}.100ms",
                {
                    "type": "change",
                    "timestamp": _ms(),
                    "instrument_name": i,
                    "change_id": self.change_id[i],
                    "prev_change_id": prev,
                    "bids": lv(db),
                    "asks": lv(da),
                },
            )
            if i in self.drop_change:
                self.drop_change.discard(i)
            else:
                self.broadcast(f"book.{i}.100ms", note)
            trades = []
            for _ in range(self.rng.randint(1, 2)):
                self.trade_seq[i] += 1
                t: dict[str, Any] = {
                    "trade_seq": self.trade_seq[i],
                    "trade_id": str(self.trade_seq[i]),
                    "timestamp": _ms(),
                    "instrument_name": i,
                    "price": 500.0,
                    "amount": 10,
                    "direction": "sell",
                    "tick_direction": 2,
                    "index_price": 500.1,
                    "mark_price": 500.0,
                }
                if self.rng.random() < 0.2:
                    t["liquidation"] = "T"
                trades.append(t)
            self.broadcast(
                f"trades.{i}.100ms",
                self._note(f"trades.{i}.100ms", trades),
                on_sent=self._count_liquidations(i, sum(1 for t in trades if "liquidation" in t)),
            )
            self.broadcast(
                f"ticker.{i}.100ms",
                self._note(
                    f"ticker.{i}.100ms",
                    {
                        "timestamp": _ms(),
                        "instrument_name": i,
                        "mark_price": 500.0,
                        "funding_8h": 0.0001,
                    },
                ),
            )
        self.broadcast(
            "deribit_volatility_index.btc_usd",
            self._note(
                "deribit_volatility_index.btc_usd",
                {"timestamp": _ms(), "volatility": 45.1, "index_name": "btc_usd"},
            ),
        )

    async def _get_time(self, request: web.Request) -> web.Response:
        self.guard()
        return web.json_response({"jsonrpc": "2.0", "result": _ms(), "usIn": 0, "usOut": 0})

    async def _get_instrument(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "jsonrpc": "2.0",
                "result": {
                    "instrument_name": request.query["instrument_name"],
                    "tick_size": 0.5,
                    "min_trade_amount": 10,
                    "contract_size": 10,
                },
            }
        )
