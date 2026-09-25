"""User data stream (plan §12.4): a listenKey from REST, the ``/private`` WebSocket route,
a keepalive every 30 minutes (the key expires after 60), and a fresh key when the exchange
says it expired. Events are parsed into small typed records; the OMS applies them
idempotently (a rotating connection may deliver an event twice).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import aiohttp
import msgspec

from quanta.core.clock import Clock
from quanta.core.log import get_logger
from quanta.net.ws import LifecycleEvent, ManagedWebSocket, WsSettings
from quanta.venues.binance_usdm.trade_rest import BinanceTradeRest, TradeError

log = get_logger(__name__)
KEEPALIVE_S = 30 * 60


@dataclass(frozen=True, slots=True)
class OrderUpdate:
    event_ms: int
    symbol: str
    client_id: str
    order_id: int
    side: str
    order_type: str
    exec_type: str  # NEW | TRADE | CANCELED | EXPIRED | CALCULATED (liquidation) | AMENDMENT
    status: str  # NEW | PARTIALLY_FILLED | FILLED | CANCELED | EXPIRED | EXPIRED_IN_MATCH
    orig_qty: Decimal
    price: Decimal
    cum_qty: Decimal
    last_qty: Decimal
    last_price: Decimal
    avg_price: Decimal
    fee: Decimal
    fee_asset: str
    trade_id: int
    realized_pnl: Decimal
    reduce_only: bool


@dataclass(frozen=True, slots=True)
class PositionUpdate:
    symbol: str
    amount: Decimal  # signed (one-way mode)
    entry_price: Decimal
    unrealized_pnl: Decimal


@dataclass(frozen=True, slots=True)
class AccountUpdate:
    event_ms: int
    reason: str
    balances: dict[str, Decimal]  # asset → wallet balance
    positions: list[PositionUpdate]


@dataclass(frozen=True, slots=True)
class AlgoUpdate:
    event_ms: int
    symbol: str
    client_algo_id: str
    status: str  # NEW | CANCELED | TRIGGERING | TRIGGERED | FINISHED | REJECTED | EXPIRED
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ListenKeyExpired:
    event_ms: int


UserEvent = OrderUpdate | AccountUpdate | AlgoUpdate | ListenKeyExpired


def _d(v: Any) -> Decimal:
    return Decimal(str(v)) if v not in (None, "") else Decimal(0)


def parse_user_event(text: str) -> UserEvent | None:
    """One frame of the user stream (plain or ``{"stream":…, "data":…}`` form)."""
    msg = msgspec.json.decode(text)
    if isinstance(msg, dict) and "data" in msg and "e" not in msg:
        msg = msg["data"]
    if not isinstance(msg, dict):
        return None
    kind = msg.get("e")
    if kind == "ORDER_TRADE_UPDATE":
        o = msg["o"]
        return OrderUpdate(
            event_ms=int(msg.get("E", 0)),
            symbol=o["s"],
            client_id=o.get("c", ""),
            order_id=int(o.get("i", 0)),
            side=o.get("S", ""),
            order_type=o.get("o", ""),
            exec_type=o.get("x", ""),
            status=o.get("X", ""),
            orig_qty=_d(o.get("q")),
            price=_d(o.get("p")),
            cum_qty=_d(o.get("z")),
            last_qty=_d(o.get("l")),
            last_price=_d(o.get("L")),
            avg_price=_d(o.get("ap")),
            fee=_d(o.get("n")),
            fee_asset=o.get("N") or "",
            trade_id=int(o.get("t", 0) or 0),
            realized_pnl=_d(o.get("rp")),
            reduce_only=bool(o.get("R", False)),
        )
    if kind == "ACCOUNT_UPDATE":
        a = msg.get("a", {})
        return AccountUpdate(
            event_ms=int(msg.get("E", 0)),
            reason=a.get("m", ""),
            balances={b["a"]: _d(b.get("wb")) for b in a.get("B", [])},
            positions=[
                PositionUpdate(p["s"], _d(p.get("pa")), _d(p.get("ep")), _d(p.get("up")))
                for p in a.get("P", [])
            ],
        )
    if kind == "ALGO_UPDATE":
        o = msg.get("o", {})
        return AlgoUpdate(
            event_ms=int(msg.get("E", 0)),
            symbol=o.get("s", ""),
            client_algo_id=o.get("caid", ""),
            status=o.get("X", ""),
            raw=o,
        )
    if kind == "listenKeyExpired":
        return ListenKeyExpired(int(msg.get("E", 0)))
    return None


class UserStream:
    """Keeps a user data stream open; ``connected`` is False while it is down."""

    def __init__(
        self,
        rest: BinanceTradeRest,
        ws_url: str,
        session: aiohttp.ClientSession,
        clock: Clock,
        on_event: Callable[[UserEvent], None],
        on_reconnect: Callable[[], None] | None = None,
        settings: WsSettings | None = None,
    ) -> None:
        self._rest = rest
        self._ws_url = ws_url.rstrip("/")
        self._session = session
        self._clock = clock
        self._on_event = on_event
        self._on_reconnect = on_reconnect
        self._settings = settings or WsSettings(heartbeat_s=20.0, idle_timeout_s=3600.0)
        self._ws: ManagedWebSocket | None = None
        self._expired = asyncio.Event()
        self.last_up_ns = 0  # last time the stream was known to be connected
        self.reconnects = 0
        self._was_connected = False

    @property
    def connected(self) -> bool:
        return self._ws is not None and self._ws.connected

    def _frame(self, conn_id: str, seq: int, recv_ns: int, text: str) -> None:
        self.last_up_ns = recv_ns
        ev = parse_user_event(text)
        if isinstance(ev, ListenKeyExpired):
            self._expired.set()
        elif ev is not None:
            self._on_event(ev)

    def _lifecycle(self, ev: LifecycleEvent) -> None:
        if ev.event == "connected":
            self.last_up_ns = ev.ts_ns
            if self._was_connected:
                self.reconnects += 1
                if self._on_reconnect:
                    self._on_reconnect()
            self._was_connected = True

    def down_for_s(self, now_ns: int) -> float:
        if self.connected:
            return 0.0
        return (now_ns - self.last_up_ns) / 1e9 if self.last_up_ns else float("inf")

    async def force_reconnect(self) -> None:
        if self._ws is not None:
            await self._ws.force_reconnect("requested")

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                key = await self._rest.new_listen_key()
            except TradeError as e:
                log.warning("listen_key_failed", error=str(e))
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), 5.0)
                continue
            self._expired.clear()
            self._ws = ManagedWebSocket(
                "user",
                f"{self._ws_url}/private/ws?listenKey={key}",
                self._session,
                self._clock,
                self._frame,
                self._lifecycle,
                self._settings,
            )
            ws_task = asyncio.create_task(self._ws.run())
            try:
                while not stop.is_set() and not self._expired.is_set():
                    waiters = [
                        asyncio.ensure_future(stop.wait()),
                        asyncio.ensure_future(self._expired.wait()),
                    ]
                    done, pending = await asyncio.wait(
                        waiters, timeout=KEEPALIVE_S, return_when=asyncio.FIRST_COMPLETED
                    )
                    for w in pending:
                        w.cancel()
                    if not done:
                        try:
                            await self._rest.keepalive_listen_key()
                        except TradeError as e:
                            log.warning("listen_key_keepalive_failed", error=str(e))
                            self._expired.set()
            finally:
                self._ws.stop()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await asyncio.wait_for(ws_task, 5.0)
            if self._expired.is_set() and not stop.is_set():
                log.info("listen_key_renew")
        with contextlib.suppress(TradeError):
            await self._rest.close_listen_key()
