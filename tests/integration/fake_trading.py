"""A fake Binance USDⓈ-M *trading* venue for integration tests of the trader.

Deterministic: prices move only when a test calls :meth:`set_price`. Implements what the
trader uses — signed REST (HMAC verified), orders (MARKET, LIMIT GTX/IOC, reduceOnly), algo
STOP_MARKET (closePosition on the mark price), countdownCancelAll, account/positionRisk,
listenKey, the ``/private`` user stream (ORDER_TRADE_UPDATE, ACCOUNT_UPDATE, ALGO_UPDATE) and
``/public`` + ``/market`` bookTicker / markPrice streams.

Fault injection: an error for the next call of a path, optionally *after* executing it (the
"503 unknown but the order exists" case), stalls (client timeout), dropped user streams and
an expired listenKey.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import itertools
import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl

from aiohttp import WSMsgType, web

D0 = Decimal(0)
TAKER = Decimal("0.0005")
MAKER = Decimal("0.0002")


def _ms() -> int:
    return int(time.time() * 1000)


def _s(d: Decimal) -> str:
    return format(d.normalize(), "f") if d else "0"


@dataclass
class Fault:
    status: int
    code: int | None
    msg: str
    execute: bool = False  # process the request anyway (execution status "unknown")


@dataclass
class Market:
    bid: Decimal
    ask: Decimal
    mark: Decimal


@dataclass
class Position:
    amount: Decimal = D0
    entry: Decimal = D0


@dataclass
class Order:
    symbol: str
    client_id: str
    order_id: int
    side: str
    type: str
    tif: str
    qty: Decimal
    price: Decimal
    reduce_only: bool
    status: str = "NEW"
    filled: Decimal = D0
    avg: Decimal = D0
    update_ms: int = 0


@dataclass
class Algo:
    symbol: str
    caid: str
    algo_id: int
    side: str
    trigger: Decimal
    close_position: bool
    qty: Decimal
    status: str = "NEW"


@dataclass
class FakeTrading:
    symbols: tuple[str, ...] = ("BTCUSDT",)
    price: Decimal = Decimal("65000")
    api_key: str = "test-key"
    secret: str = "test-secret"
    balance: Decimal = Decimal("10000")
    countdown_cancels_algo: bool = False
    stream_interval_s: float = 0.2
    market: dict[str, Market] = field(default_factory=dict)
    positions: dict[str, Position] = field(default_factory=dict)
    orders: dict[str, Order] = field(default_factory=dict)
    algos: dict[str, Algo] = field(default_factory=dict)
    faults: dict[str, list[Fault]] = field(default_factory=dict)
    stalls: dict[str, float] = field(default_factory=dict)
    countdowns: dict[str, int] = field(default_factory=dict)
    requests: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)
    listen_keys: set[str] = field(default_factory=set)
    leverage: dict[str, int] = field(default_factory=dict)
    margin_type: dict[str, str] = field(default_factory=dict)
    dual_side: bool = True  # the trader must switch it to one-way
    streams_paused: bool = False
    port: int = 0

    def __post_init__(self) -> None:
        half = Decimal("0.05")
        for s in self.symbols:
            self.market[s] = Market(self.price - half, self.price + half, self.price)
            self.positions[s] = Position()
        self._ids = itertools.count(1000)
        self._trade_ids = itertools.count(5000)
        self._keys = itertools.count(1)
        self._private: list[tuple[web.WebSocketResponse, asyncio.Queue[str]]] = []
        self._muted: list[dict[str, Any]] | None = None
        self._held: list[dict[str, Any]] = []
        self._public: list[tuple[web.WebSocketResponse, set[str], asyncio.Queue[str]]] = []
        app = web.Application()
        r = app.router
        r.add_get("/fapi/v1/time", self._time)
        r.add_get("/fapi/v1/exchangeInfo", self._exchange_info)
        r.add_get("/fapi/v1/ticker/bookTicker", self._book_ticker)
        r.add_get("/fapi/v1/premiumIndex", self._premium)
        r.add_route("*", "/fapi/v1/order", self._order)
        r.add_delete("/fapi/v1/allOpenOrders", self._cancel_all)
        r.add_get("/fapi/v1/openOrders", self._open_orders)
        r.add_post("/fapi/v1/countdownCancelAll", self._countdown)
        r.add_route("*", "/fapi/v1/algoOrder", self._algo_order)
        r.add_delete("/fapi/v1/algoOpenOrders", self._cancel_all_algo)
        r.add_get("/fapi/v1/openAlgoOrders", self._open_algo)
        r.add_get("/fapi/v3/account", self._account)
        r.add_get("/fapi/v3/positionRisk", self._position_risk)
        r.add_post("/fapi/v1/leverage", self._leverage)
        r.add_post("/fapi/v1/marginType", self._margin_type)
        r.add_post("/fapi/v1/positionSide/dual", self._dual)
        r.add_route("*", "/fapi/v1/listenKey", self._listen_key)
        r.add_get("/private/ws", self._private_ws)
        r.add_get("/public/stream", self._public_ws)
        r.add_get("/market/stream", self._public_ws)
        self.app = app
        self._runner: web.AppRunner | None = None
        self._task: asyncio.Task[None] | None = None

    # -- lifecycle -------------------------------------------------------------------------
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
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        for ws, _ in list(self._private):
            await ws.close()
        for ws, _, _ in list(self._public):
            await ws.close()
        if self._runner:
            await self._runner.cleanup()

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.stream_interval_s)
            now = _ms()
            for s, deadline in list(self.countdowns.items()):
                if now >= deadline:
                    del self.countdowns[s]
                    self._cancel_symbol(s, algo=self.countdown_cancels_algo)
            if not self.streams_paused:
                for s in self.symbols:
                    self._publish_market(s)

    # -- test controls ---------------------------------------------------------------------
    def fault(
        self, path: str, status: int, code: int | None, msg: str, execute: bool = False
    ) -> None:
        self.faults.setdefault(path, []).append(Fault(status, code, msg, execute))

    def set_price(self, symbol: str, mid: float, mark: float | None = None) -> None:
        m = self.market[symbol]
        mid_d = Decimal(str(mid))
        m.bid, m.ask = mid_d - Decimal("0.05"), mid_d + Decimal("0.05")
        m.mark = Decimal(str(mark)) if mark is not None else mid_d
        self._match(symbol)
        self._publish_market(symbol)

    async def drop_private(self) -> None:
        for ws, _ in list(self._private):
            await ws.close()

    def expire_listen_keys(self) -> None:
        self.listen_keys.clear()
        self._push_private({"e": "listenKeyExpired", "E": _ms()})

    def open_orders_of(self, symbol: str) -> list[Order]:
        return [
            o
            for o in self.orders.values()
            if o.symbol == symbol and o.status in ("NEW", "PARTIALLY_FILLED")
        ]

    def open_algos_of(self, symbol: str) -> list[Algo]:
        return [a for a in self.algos.values() if a.symbol == symbol and a.status == "NEW"]

    # -- request plumbing ------------------------------------------------------------------
    async def _params(self, request: web.Request, signed: bool = True) -> dict[str, str]:
        raw = request.query_string
        params = dict(parse_qsl(raw, keep_blank_values=True))
        self.requests.append((request.method, request.path, params))
        if request.path in self.stalls:
            await asyncio.sleep(self.stalls.pop(request.path))
        if signed:
            if request.headers.get("X-MBX-APIKEY") != self.api_key:
                raise self._error(401, -2015, "Invalid API-key, IP, or permissions for action.")
            payload, _, sig = raw.rpartition("&signature=")
            good = hmac.new(self.secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
            if sig != good:
                raise self._error(400, -1022, "Signature for this request is not valid.")
            if abs(int(params["timestamp"]) - _ms()) > int(params.get("recvWindow", 5000)):
                raise self._error(
                    400, -1021, "Timestamp for this request is outside of the recvWindow."
                )
        return params

    def _take_fault(self, path: str) -> Fault | None:
        q = self.faults.get(path)
        return q.pop(0) if q else None

    @staticmethod
    def _error(status: int, code: int | None, msg: str) -> web.HTTPException:
        body = json.dumps({"code": code, "msg": msg}) if code is not None else msg
        return _http(status, body)

    # -- market ----------------------------------------------------------------------------
    async def _time(self, request: web.Request) -> web.Response:
        return web.json_response({"serverTime": _ms()})

    async def _exchange_info(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "symbols": [
                    {
                        "symbol": s,
                        "status": "TRADING",
                        "filters": [
                            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                            {
                                "filterType": "LOT_SIZE",
                                "stepSize": "0.001",
                                "minQty": "0.001",
                                "maxQty": "1000",
                            },
                            {
                                "filterType": "MARKET_LOT_SIZE",
                                "stepSize": "0.001",
                                "minQty": "0.001",
                                "maxQty": "120",
                            },
                            {"filterType": "MIN_NOTIONAL", "notional": "100"},
                        ],
                    }
                    for s in self.symbols
                ]
            }
        )

    async def _book_ticker(self, request: web.Request) -> web.Response:
        m = self.market[request.query["symbol"]]
        return web.json_response(
            {"symbol": request.query["symbol"], "bidPrice": _s(m.bid), "askPrice": _s(m.ask)}
        )

    async def _premium(self, request: web.Request) -> web.Response:
        m = self.market[request.query["symbol"]]
        return web.json_response({"symbol": request.query["symbol"], "markPrice": _s(m.mark)})

    def _publish_market(self, s: str) -> None:
        m, now = self.market[s], _ms()
        low = s.lower()
        self._push_public(
            f"{low}@bookTicker",
            {
                "e": "bookTicker",
                "E": now,
                "T": now,
                "s": s,
                "u": now,
                "b": _s(m.bid),
                "B": "1.000",
                "a": _s(m.ask),
                "A": "1.000",
            },
        )
        self._push_public(
            f"{low}@markPrice@1s",
            {
                "e": "markPriceUpdate",
                "E": now,
                "s": s,
                "p": _s(m.mark),
                "i": _s(m.mark),
                "P": _s(m.mark),
                "r": "0.0001",
                "T": now + 3_600_000,
            },
        )

    def _push_public(self, stream: str, data: dict[str, Any]) -> None:
        text = json.dumps({"stream": stream, "data": data})
        for _, streams, q in self._public:
            if stream in streams:
                q.put_nowait(text)

    async def _public_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        streams = set(request.query.get("streams", "").split("/"))
        q: asyncio.Queue[str] = asyncio.Queue()
        entry = (ws, streams, q)
        self._public.append(entry)
        await self._pump(ws, q)
        self._public.remove(entry)
        return ws

    async def _pump(self, ws: web.WebSocketResponse, q: asyncio.Queue[str]) -> None:
        async def sender() -> None:
            while not ws.closed:
                text = await q.get()
                with contextlib.suppress(ConnectionError, RuntimeError):
                    await ws.send_str(text)

        task = asyncio.create_task(sender())
        try:
            async for msg in ws:
                if msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break
        finally:
            task.cancel()

    # -- user stream -----------------------------------------------------------------------
    async def _listen_key(self, request: web.Request) -> web.Response:
        await self._params(request, signed=False)
        if request.headers.get("X-MBX-APIKEY") != self.api_key:
            raise self._error(401, -2015, "Invalid API-key, IP, or permissions for action.")
        if request.method == "POST":
            key = f"lk{next(self._keys)}"
            self.listen_keys.add(key)
            return web.json_response({"listenKey": key})
        return web.json_response({})

    async def _private_ws(self, request: web.Request) -> web.WebSocketResponse:
        if request.query.get("listenKey") not in self.listen_keys:
            raise web.HTTPBadRequest()
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        q: asyncio.Queue[str] = asyncio.Queue()
        entry = (ws, q)
        self._private.append(entry)
        await self._pump(ws, q)
        self._private.remove(entry)
        return ws

    def mute_private(self, muted: bool) -> None:
        """Hold user-stream events back (True) and deliver them later (False)."""
        self._muted = [] if muted else None
        if not muted:
            for data in self._held:
                self._push_private(data)
            self._held = []

    def _push_private(self, data: dict[str, Any]) -> None:
        if self._muted is not None:
            self._held.append(data)
            return
        text = json.dumps(data)
        for _, q in self._private:
            q.put_nowait(text)

    def _order_event(
        self,
        o: Order,
        exec_type: str,
        last_qty: Decimal = D0,
        last_px: Decimal = D0,
        fee: Decimal = D0,
        rp: Decimal = D0,
        trade_id: int = 0,
    ) -> None:
        self._push_private(
            {
                "e": "ORDER_TRADE_UPDATE",
                "E": _ms(),
                "T": _ms(),
                "o": {
                    "s": o.symbol,
                    "c": o.client_id,
                    "S": o.side,
                    "o": o.type,
                    "f": o.tif,
                    "q": _s(o.qty),
                    "p": _s(o.price),
                    "ap": _s(o.avg),
                    "sp": "0",
                    "x": exec_type,
                    "X": o.status,
                    "i": o.order_id,
                    "l": _s(last_qty),
                    "z": _s(o.filled),
                    "L": _s(last_px),
                    "N": "USDT",
                    "n": _s(fee),
                    "T": _ms(),
                    "t": trade_id,
                    "R": o.reduce_only,
                    "rp": _s(rp),
                    "ps": "BOTH",
                },
            }
        )

    def _account_event(self, s: str) -> None:
        p = self.positions[s]
        self._push_private(
            {
                "e": "ACCOUNT_UPDATE",
                "E": _ms(),
                "T": _ms(),
                "a": {
                    "m": "ORDER",
                    "B": [{"a": "USDT", "wb": _s(self.balance), "cw": _s(self.balance)}],
                    "P": [
                        {
                            "s": s,
                            "pa": _s(p.amount),
                            "ep": _s(p.entry),
                            "up": _s(self._upnl(s)),
                            "mt": "isolated",
                            "ps": "BOTH",
                        }
                    ],
                },
            }
        )

    def _algo_event(self, a: Algo) -> None:
        self._push_private(
            {
                "e": "ALGO_UPDATE",
                "E": _ms(),
                "T": _ms(),
                "o": {
                    "caid": a.caid,
                    "aid": a.algo_id,
                    "at": "CONDITIONAL",
                    "o": "STOP_MARKET",
                    "s": a.symbol,
                    "S": a.side,
                    "X": a.status,
                    "tp": _s(a.trigger),
                    "cp": a.close_position,
                    "wt": "MARK_PRICE",
                },
            }
        )

    # -- trading logic ---------------------------------------------------------------------
    def _upnl(self, s: str) -> Decimal:
        p = self.positions[s]
        return (self.market[s].mark - p.entry) * p.amount if p.amount else D0

    def _fill(self, o: Order, qty: Decimal, px: Decimal, maker: bool) -> None:
        pos = self.positions[o.symbol]
        signed = qty if o.side == "BUY" else -qty
        rp = D0
        if pos.amount and (pos.amount > 0) != (signed > 0):  # reducing
            closed = min(abs(signed), abs(pos.amount))
            rp = (px - pos.entry) * closed * (1 if pos.amount > 0 else -1)
            new = pos.amount + signed
            if new == 0:
                pos.entry = D0
            elif (new > 0) != (pos.amount > 0):  # flipped
                pos.entry = px
            pos.amount = new
        else:
            total = abs(pos.amount) + qty
            pos.entry = (pos.entry * abs(pos.amount) + px * qty) / total
            pos.amount += signed
        fee = qty * px * (MAKER if maker else TAKER)
        self.balance += rp - fee
        o.avg = (o.avg * o.filled + px * qty) / (o.filled + qty)
        o.filled += qty
        o.status = "FILLED" if o.filled >= o.qty else "PARTIALLY_FILLED"
        o.update_ms = _ms()
        self._order_event(o, "TRADE", qty, px, fee, rp, next(self._trade_ids))
        self._account_event(o.symbol)

    def _match(self, s: str) -> None:
        m = self.market[s]
        for o in self.open_orders_of(s):
            buy_hit = o.side == "BUY" and m.ask <= o.price
            sell_hit = o.side == "SELL" and m.bid >= o.price
            if o.type == "LIMIT" and (buy_hit or sell_hit):
                self._fill(o, o.qty - o.filled, o.price, maker=True)
        for a in self.open_algos_of(s):
            hit = (a.side == "SELL" and m.mark <= a.trigger) or (
                a.side == "BUY" and m.mark >= a.trigger
            )
            if not hit:
                continue
            a.status = "TRIGGERED"
            self._algo_event(a)
            pos = self.positions[s].amount
            qty = abs(pos) if a.close_position else a.qty
            if qty > 0 and (not a.close_position or (pos > 0) == (a.side == "SELL")):
                o = Order(
                    s,
                    f"algo-{a.algo_id}",
                    next(self._ids),
                    a.side,
                    "MARKET",
                    "GTC",
                    qty,
                    D0,
                    a.close_position,
                )
                self.orders[o.client_id] = o
                self._order_event(o, "NEW")
                self._fill(o, qty, m.bid if a.side == "SELL" else m.ask, maker=False)
            a.status = "FINISHED"
            self._algo_event(a)

    def _cancel_symbol(self, s: str, algo: bool) -> None:
        for o in self.open_orders_of(s):
            o.status = "CANCELED"
            self._order_event(o, "CANCELED")
        if algo:
            for a in self.open_algos_of(s):
                a.status = "CANCELED"
                self._algo_event(a)

    def _order_json(self, o: Order) -> dict[str, Any]:
        return {
            "symbol": o.symbol,
            "clientOrderId": o.client_id,
            "orderId": o.order_id,
            "side": o.side,
            "type": o.type,
            "timeInForce": o.tif,
            "origQty": _s(o.qty),
            "price": _s(o.price),
            "executedQty": _s(o.filled),
            "avgPrice": _s(o.avg),
            "status": o.status,
            "reduceOnly": o.reduce_only,
            "updateTime": o.update_ms,
        }

    def _place(self, p: dict[str, str]) -> Order:
        s = p["symbol"]
        cid = p["newClientOrderId"]
        if cid in self.orders and self.orders[cid].status in ("NEW", "PARTIALLY_FILLED"):
            raise self._error(400, -4116, "ClientOrderId is duplicated.")
        if p["type"] not in ("LIMIT", "MARKET"):
            raise self._error(
                400,
                -4120,
                "Order type not supported for this endpoint. Please use the Algo Order API "
                "endpoints instead.",
            )
        qty = Decimal(p["quantity"])
        o = Order(
            s,
            cid,
            next(self._ids),
            p["side"],
            p["type"],
            p.get("timeInForce", "GTC"),
            qty,
            Decimal(p.get("price", "0")),
            p.get("reduceOnly") == "true",
            update_ms=_ms(),
        )
        pos = self.positions[s].amount
        m = self.market[s]
        if o.reduce_only:
            if pos == 0 or (pos > 0) == (o.side == "BUY"):
                raise self._error(400, -2022, "ReduceOnly Order is rejected.")
            o.qty = min(o.qty, abs(pos))
        crosses = (o.side == "BUY" and o.price >= m.ask) or (o.side == "SELL" and o.price <= m.bid)
        if o.type == "LIMIT" and o.tif == "GTX" and crosses:
            raise self._error(
                400,
                -5022,
                "Due to the order could not be executed as maker, the Post Only order will be "
                "rejected.",
            )
        self.orders[cid] = o
        self._order_event(o, "NEW")
        if o.type == "MARKET":
            self._fill(o, o.qty, m.ask if o.side == "BUY" else m.bid, maker=False)
        elif o.tif == "IOC":
            if crosses:
                self._fill(o, o.qty, m.ask if o.side == "BUY" else m.bid, maker=False)
            else:
                o.status = "EXPIRED"
                self._order_event(o, "EXPIRED")
        return o

    async def _order(self, request: web.Request) -> web.Response:
        p = await self._params(request)
        f = self._take_fault("/fapi/v1/order") if request.method == "POST" else None
        if request.method == "POST":
            if f and not f.execute:
                raise self._error(f.status, f.code, f.msg)
            o = self._place(p)
            if f:
                raise self._error(f.status, f.code, f.msg)
            return web.json_response(self._order_json(o))
        cid = p.get("origClientOrderId", "")
        o2 = self.orders.get(cid)
        if o2 is None:
            raise self._error(400, -2013, "Order does not exist.")
        if request.method == "DELETE":
            if o2.status not in ("NEW", "PARTIALLY_FILLED"):
                raise self._error(400, -2011, "Unknown order sent.")
            o2.status = "CANCELED"
            self._order_event(o2, "CANCELED")
        return web.json_response(self._order_json(o2))

    async def _cancel_all(self, request: web.Request) -> web.Response:
        p = await self._params(request)
        self._cancel_symbol(p["symbol"], algo=False)
        return web.json_response(
            {"code": 200, "msg": "The operation of cancel all open order is done."}
        )

    async def _open_orders(self, request: web.Request) -> web.Response:
        p = await self._params(request)
        syms = [p["symbol"]] if p.get("symbol") else list(self.symbols)
        return web.json_response(
            [self._order_json(o) for s in syms for o in self.open_orders_of(s)]
        )

    async def _countdown(self, request: web.Request) -> web.Response:
        p = await self._params(request)
        ms = int(p["countdownTime"])
        if ms == 0:
            self.countdowns.pop(p["symbol"], None)
        else:
            self.countdowns[p["symbol"]] = _ms() + ms
        return web.json_response({"symbol": p["symbol"], "countdownTime": str(ms)})

    async def _algo_order(self, request: web.Request) -> web.Response:
        p = await self._params(request)
        if request.method == "POST":
            f = self._take_fault("/fapi/v1/algoOrder")
            if f and not f.execute:
                raise self._error(f.status, f.code, f.msg)
            m = self.market[p["symbol"]]
            trig = Decimal(p["triggerPrice"])
            if (p["side"] == "SELL" and m.mark <= trig) or (p["side"] == "BUY" and m.mark >= trig):
                raise self._error(400, -2021, "Order would immediately trigger.")
            a = Algo(
                p["symbol"],
                p["clientAlgoId"],
                next(self._ids),
                p["side"],
                trig,
                p.get("closePosition") == "true",
                Decimal(p.get("quantity", "0")),
            )
            self.algos[a.caid] = a
            self._algo_event(a)
            if f:
                raise self._error(f.status, f.code, f.msg)
            return web.json_response(
                {"algoId": a.algo_id, "clientAlgoId": a.caid, "algoStatus": a.status}
            )
        a2 = self.algos.get(p.get("clientAlgoId", ""))
        if a2 is None or a2.status != "NEW":
            raise self._error(400, -2011, "Unknown order sent.")
        a2.status = "CANCELED"
        self._algo_event(a2)
        return web.json_response({"clientAlgoId": a2.caid, "code": "200"})

    async def _cancel_all_algo(self, request: web.Request) -> web.Response:
        p = await self._params(request)
        for a in self.open_algos_of(p["symbol"]):
            a.status = "CANCELED"
            self._algo_event(a)
        return web.json_response({"code": 200})

    async def _open_algo(self, request: web.Request) -> web.Response:
        p = await self._params(request)
        syms = [p["symbol"]] if p.get("symbol") else list(self.symbols)
        return web.json_response(
            [
                {
                    "clientAlgoId": a.caid,
                    "algoId": a.algo_id,
                    "symbol": a.symbol,
                    "side": a.side,
                    "orderType": "STOP_MARKET",
                    "triggerPrice": _s(a.trigger),
                    "closePosition": a.close_position,
                    "algoStatus": a.status,
                }
                for s in syms
                for a in self.open_algos_of(s)
            ]
        )

    async def _account(self, request: web.Request) -> web.Response:
        await self._params(request)
        upnl = sum((self._upnl(s) for s in self.symbols), D0)
        return web.json_response(
            {
                "totalWalletBalance": _s(self.balance),
                "totalUnrealizedProfit": _s(upnl),
                "totalMarginBalance": _s(self.balance + upnl),
                "availableBalance": _s(self.balance),
                "assets": [
                    {
                        "asset": "USDT",
                        "walletBalance": _s(self.balance),
                        "unrealizedProfit": _s(upnl),
                        "marginBalance": _s(self.balance + upnl),
                    }
                ],
                "positions": [
                    {
                        "symbol": s,
                        "positionSide": "BOTH",
                        "positionAmt": _s(p.amount),
                        "unrealizedProfit": _s(self._upnl(s)),
                    }
                    for s, p in self.positions.items()
                    if p.amount
                ],
            }
        )

    async def _position_risk(self, request: web.Request) -> web.Response:
        p = await self._params(request)
        syms = [p["symbol"]] if p.get("symbol") else list(self.symbols)
        return web.json_response(
            [
                {
                    "symbol": s,
                    "positionSide": "BOTH",
                    "positionAmt": _s(self.positions[s].amount),
                    "entryPrice": _s(self.positions[s].entry),
                    "markPrice": _s(self.market[s].mark),
                    "unRealizedProfit": _s(self._upnl(s)),
                }
                for s in syms
                if self.positions[s].amount
            ]
        )

    async def _leverage(self, request: web.Request) -> web.Response:
        p = await self._params(request)
        self.leverage[p["symbol"]] = int(p["leverage"])
        return web.json_response({"symbol": p["symbol"], "leverage": int(p["leverage"])})

    async def _margin_type(self, request: web.Request) -> web.Response:
        p = await self._params(request)
        if self.margin_type.get(p["symbol"]) == p["marginType"]:
            raise self._error(400, -4046, "No need to change margin type.")
        self.margin_type[p["symbol"]] = p["marginType"]
        return web.json_response({"code": 200, "msg": "success"})

    async def _dual(self, request: web.Request) -> web.Response:
        p = await self._params(request)
        want = p["dualSidePosition"] == "true"
        if want == self.dual_side:
            raise self._error(400, -4059, "No need to change position side.")
        self.dual_side = want
        return web.json_response({"code": 200, "msg": "success"})


def _http(status: int, body: str) -> web.HTTPException:
    ctype = "application/json" if body.startswith("{") else "text/plain"
    exc_cls = {
        400: web.HTTPBadRequest,
        401: web.HTTPUnauthorized,
        408: web.HTTPRequestTimeout,
        429: web.HTTPTooManyRequests,
        500: web.HTTPInternalServerError,
        502: web.HTTPBadGateway,
        503: web.HTTPServiceUnavailable,
    }.get(status, web.HTTPBadRequest)
    return exc_cls(text=body, content_type=ctype)
