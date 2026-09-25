"""Order management (design §12.3).

* **Write-ahead:** an intent and its order row are on disk before the request leaves.
* **Duplicate protection:** an intent may carry a key; a second intent with the same key is
  refused. clientOrderIds are ``q{epoch}-{intent}-{attempt}`` and never repeat.
* **Unknown outcome** (timeout, 503 "unknown", -1007): the order becomes UNKNOWN, its symbol
  is frozen (no new orders) and the order is *queried* until the exchange says what happened.
  Nothing is ever re-sent automatically.
* **User stream first, REST second:** stream reports and REST answers go through the same
  state machine; fills are recorded once by (symbol, trade id), also for orders we did not
  send (a triggered stop, a manual order).
* **Reconciliation:** open orders, open algo orders and positions are compared with the
  exchange; anything we did not know about, or a position that differs, is reported as a
  problem (the kill switch pauses new entries) and the exchange's view is adopted.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from quanta.core.clock import Clock
from quanta.core.log import get_logger
from quanta.oms.state import EXCHANGE, OPEN, TERMINAL, IllegalTransition, OrderStatus
from quanta.oms.store import Store
from quanta.venues.binance_usdm.errors import Action
from quanta.venues.binance_usdm.filters import fmt
from quanta.venues.binance_usdm.trade_rest import BinanceTradeRest, TradeError
from quanta.venues.binance_usdm.user_stream import (
    AccountUpdate,
    AlgoUpdate,
    OrderUpdate,
    UserEvent,
)

log = get_logger(__name__)
UNKNOWN_GRACE_NS = 10 * 10**9  # an order still not found after this never reached the book
TRIGGER_WINDOW_NS = 30 * 10**9  # orders appearing this soon after our stop fired are its fill

ProblemHandler = Callable[[str, str], None]  # (problem kind, detail)


@dataclass(frozen=True, slots=True)
class Intent:
    purpose: str  # entry | exit | protect | flatten | drill
    symbol: str
    side: str  # BUY | SELL
    order_type: str  # LIMIT | MARKET | STOP_MARKET
    qty: Decimal | None = None
    price: Decimal | None = None
    tif: str | None = None  # GTX | IOC | GTC
    reduce_only: bool = False
    trigger: Decimal | None = None  # STOP_MARKET (algo) trigger on the mark price
    close_position: bool = False
    key: str | None = None  # idempotency key

    @property
    def algo(self) -> bool:
        return self.order_type == "STOP_MARKET"


@dataclass(frozen=True, slots=True)
class SubmitResult:
    client_id: str | None
    status: OrderStatus
    error: str | None = None
    action: Action | None = None

    @property
    def ok(self) -> bool:
        return self.status not in (OrderStatus.REJECTED, OrderStatus.UNKNOWN)


class Oms:
    def __init__(
        self,
        store: Store,
        rest: BinanceTradeRest,
        clock: Clock,
        on_problem: ProblemHandler,
    ) -> None:
        self.store = store
        self.rest = rest
        self.clock = clock
        self.on_problem = on_problem
        self.frozen: dict[str, str] = {}  # symbol → reason
        self.exchange_positions: dict[str, Decimal] = {}
        self.balances: dict[str, Decimal] = {}
        self._triggered: dict[str, int] = {}  # symbol → ns our protective stop fired
        self.last_ack_ms: float | None = None

    def client_id(self, intent_id: int, attempt: int = 1) -> str:
        return f"q{self.store.epoch}-{intent_id}-{attempt}"

    # -- sending ---------------------------------------------------------------------------
    async def submit(self, intent: Intent) -> SubmitResult:
        now = self.clock.now_ns()
        if intent.key and self.store.intent_exists(intent.key):
            return SubmitResult(None, OrderStatus.REJECTED, f"duplicate intent {intent.key}")
        if intent.symbol in self.frozen:
            reason = self.frozen[intent.symbol]
            return SubmitResult(None, OrderStatus.REJECTED, f"{intent.symbol} frozen: {reason}")
        qty = fmt(intent.qty) if intent.qty is not None else None
        price = fmt(intent.price) if intent.price is not None else None
        trig = fmt(intent.trigger) if intent.trigger is not None else None
        iid = self.store.add_intent(
            now,
            intent.key,
            intent.purpose,
            intent.symbol,
            intent.side,
            intent.order_type,
            intent.tif,
            qty,
            price,
            trig,
            intent.reduce_only,
            intent.algo,
        )
        cid = self.client_id(iid)
        self.store.add_order(
            now,
            cid,
            iid,
            intent.symbol,
            intent.side,
            intent.order_type,
            intent.purpose,
            intent.algo,
            qty,
            price or trig,
        )
        self.store.update_order(now, cid, OrderStatus.SENT, sent=True)
        try:
            if intent.algo and intent.close_position:
                assert trig is not None
                resp = await self.rest.new_stop_close(intent.symbol, intent.side, trig, cid)
            elif intent.algo:
                assert trig is not None and qty is not None
                resp = await self.rest.new_stop_order(intent.symbol, intent.side, trig, qty, cid)
            else:
                resp = await self.rest.new_order(
                    intent.symbol,
                    intent.side,
                    intent.order_type,
                    cid,
                    qty,
                    price,
                    intent.tif,
                    True if intent.reduce_only else None,
                )
        except TradeError as e:
            return self._failed(cid, intent, e)
        done = self.clock.now_ns()
        self.last_ack_ms = (done - now) / 1e6
        status = EXCHANGE.get(
            str(resp.get("status") or resp.get("algoStatus") or "NEW"), OrderStatus.NEW
        )
        row = self.store.update_order(
            done,
            cid,
            status,
            filled=Decimal(str(resp.get("executedQty", "0"))),
            avg_price=Decimal(str(resp.get("avgPrice", "0") or "0")),
            exchange_id=str(resp.get("orderId") or resp.get("algoId") or ""),
            ack=True,
        )
        self.store.journal(
            done,
            "order_sent",
            client_id=cid,
            purpose=intent.purpose,
            symbol=intent.symbol,
            side=intent.side,
            type=intent.order_type,
            qty=qty,
            price=price or trig,
            status=row.status if row else status,
            ack_ms=round(self.last_ack_ms, 1),
        )
        return SubmitResult(cid, row.status if row else status)

    def _failed(self, cid: str, intent: Intent, e: TradeError) -> SubmitResult:
        now = self.clock.now_ns()
        if e.action is Action.UNKNOWN:
            row = self.store.update_order(now, cid, OrderStatus.UNKNOWN, error=str(e))
            if row is not None and row.status is not OrderStatus.UNKNOWN:
                # the user stream already told us what happened to it
                return SubmitResult(cid, row.status, str(e), e.action)
            self.frozen[intent.symbol] = f"order {cid} outcome unknown"
            self.store.journal(now, "order_unknown", client_id=cid, error=str(e))
            self.on_problem("unknown_order", f"{cid}: {e.msg or e.status}")
            return SubmitResult(cid, OrderStatus.UNKNOWN, str(e), e.action)
        self.store.update_order(now, cid, OrderStatus.REJECTED, error=str(e))
        self.store.journal(
            now, "order_rejected", client_id=cid, code=e.code, error=e.msg, action=e.action.value
        )
        if e.action in (Action.AUTH, Action.CLOCK):
            self.on_problem(e.action.value, e.msg)
        return SubmitResult(cid, OrderStatus.REJECTED, str(e), e.action)

    async def cancel(self, client_id: str) -> bool:
        row = self.store.order(client_id)
        if row is None or row.status in TERMINAL:
            return True
        try:
            if row.algo:
                await self.rest.cancel_algo_order(client_id)
                self._report(client_id, OrderStatus.CANCELED)
            else:
                resp = await self.rest.cancel_order(row.symbol, client_id)
                self._report(
                    client_id,
                    EXCHANGE.get(str(resp.get("status")), OrderStatus.CANCELED),
                    filled=Decimal(str(resp.get("executedQty", "0"))),
                )
            return True
        except TradeError as e:
            if e.action is Action.NOT_FOUND:
                await self._query(row)  # already done: learn how it ended
                return True
            log.warning("cancel_failed", client_id=client_id, error=str(e))
            return False

    # -- reports ---------------------------------------------------------------------------
    def _report(self, client_id: str, status: OrderStatus, **kw: Any) -> None:
        try:
            self.store.update_order(self.clock.now_ns(), client_id, status, **kw)
        except IllegalTransition as exc:
            self.store.journal(self.clock.now_ns(), "anomaly", client_id=client_id, error=str(exc))
            self.on_problem("anomaly", f"{client_id}: {exc}")

    def on_event(self, ev: UserEvent) -> None:
        now = self.clock.now_ns()
        if isinstance(ev, OrderUpdate):
            if ev.exec_type == "TRADE" and ev.last_qty > 0:
                new = self.store.add_fill(
                    ev.symbol,
                    ev.trade_id,
                    ev.client_id,
                    ev.side,
                    ev.last_qty,
                    ev.last_price,
                    ev.fee,
                    ev.realized_pnl,
                    ev.event_ms,
                )
                if new:
                    self.store.journal(
                        now,
                        "fill",
                        client_id=ev.client_id,
                        symbol=ev.symbol,
                        side=ev.side,
                        qty=ev.last_qty,
                        price=ev.last_price,
                        fee=ev.fee,
                        realized=ev.realized_pnl,
                    )
            if self.store.order(ev.client_id) is not None:
                self._report(
                    ev.client_id,
                    EXCHANGE.get(ev.status, OrderStatus.NEW),
                    filled=ev.cum_qty,
                    avg_price=ev.avg_price,
                    exchange_id=str(ev.order_id),
                    ack=True,
                )
            elif ev.exec_type == "NEW":
                fired = self._triggered.get(ev.symbol, 0)
                if now - fired > TRIGGER_WINDOW_NS:
                    self.store.journal(
                        now,
                        "external_order",
                        client_id=ev.client_id,
                        symbol=ev.symbol,
                        side=ev.side,
                        type=ev.order_type,
                    )
                    self.on_problem("external_order", f"{ev.symbol} {ev.client_id}")
        elif isinstance(ev, AlgoUpdate):
            if ev.status in ("TRIGGERING", "TRIGGERED"):
                self._triggered[ev.symbol] = now
            if self.store.order(ev.client_algo_id) is not None:
                self._report(ev.client_algo_id, EXCHANGE.get(ev.status, OrderStatus.NEW), ack=True)
            self.store.journal(now, "algo_update", client_id=ev.client_algo_id, status=ev.status)
        elif isinstance(ev, AccountUpdate):
            for p in ev.positions:
                self.exchange_positions[p.symbol] = p.amount
            self.balances.update(ev.balances)

    # -- unknown outcomes ------------------------------------------------------------------
    async def _query(self, row: Any) -> None:
        if row.algo:
            opened = {a.get("clientAlgoId") for a in await self.rest.open_algo_orders(row.symbol)}
            if row.client_id in opened:
                self._report(row.client_id, OrderStatus.NEW)
            elif self.clock.now_ns() - row.created_ns > UNKNOWN_GRACE_NS:
                # not armed on the exchange: either never placed or already finished
                self._report(row.client_id, OrderStatus.EXPIRED, error="not open at the exchange")
            return
        try:
            r = await self.rest.query_order(row.symbol, row.client_id)
        except TradeError as e:
            if (
                e.action is Action.NOT_FOUND
                and self.clock.now_ns() - row.created_ns > UNKNOWN_GRACE_NS
            ):
                self._report(
                    row.client_id, OrderStatus.REJECTED, error="never reached the exchange"
                )
            return
        self._report(
            row.client_id,
            EXCHANGE.get(str(r.get("status")), OrderStatus.NEW),
            filled=Decimal(str(r.get("executedQty", "0"))),
            avg_price=Decimal(str(r.get("avgPrice", "0") or "0")),
            exchange_id=str(r.get("orderId", "")),
            ack=True,
        )

    async def resolve_unknown(self) -> None:
        now = self.clock.now_ns()
        for row in self.store.unresolved():
            if row.status is OrderStatus.SENT and now - row.updated_ns < 5 * 10**9:
                continue  # an answer may still come
            try:
                await self._query(row)
            except TradeError as e:
                log.warning("query_failed", client_id=row.client_id, error=str(e))
        pending = {r.symbol for r in self.store.unresolved()}
        for sym in list(self.frozen):
            if sym not in pending:
                del self.frozen[sym]
                self.store.journal(now, "symbol_unfrozen", symbol=sym)

    # -- reconciliation --------------------------------------------------------------------
    async def reconcile(self, symbols: list[str]) -> dict[str, Any]:
        """Compare our view with the exchange; returns what was found (and fixed)."""
        now = self.clock.now_ns()
        report: dict[str, Any] = {
            "ts_ns": now,
            "external": [],
            "closed_while_away": [],
            "position_diff": {},
            "ok": True,
        }
        ex_orders = {o["clientOrderId"]: o for o in await self.rest.open_orders()}
        ex_algos = {a["clientAlgoId"]: a for a in await self.rest.open_algo_orders()}
        ex_pos = {p["symbol"]: Decimal(p["positionAmt"]) for p in await self.rest.position_risk()}
        ours = {r.client_id for r in self.store.orders()}
        for row in self.store.orders(OPEN | {OrderStatus.SENT, OrderStatus.UNKNOWN}):
            live = row.client_id in (ex_algos if row.algo else ex_orders)
            if not live:
                await self._query(row)
                report["closed_while_away"].append(row.client_id)
        for cid in list(ex_orders) + list(ex_algos):
            if cid not in ours:
                report["external"].append(cid)
        local = self.store.positions()
        for sym in set(local) | set(ex_pos):
            diff = ex_pos.get(sym, Decimal(0)) - local.get(sym, Decimal(0))
            if diff:
                report["position_diff"][sym] = str(diff)
                self.store.adjust_position(sym, diff)
        self.exchange_positions = ex_pos
        if report["external"]:
            report["ok"] = False
            self.on_problem("external_order", ", ".join(report["external"]))
        if report["position_diff"]:
            report["ok"] = False
            self.on_problem("position_mismatch", str(report["position_diff"]))
        self.store.journal(now, "reconcile", **{k: v for k, v in report.items() if k != "ts_ns"})
        self.store.set_json("last_reconcile", report)
        return report

    def open_protective(self, symbol: str) -> list[str]:
        return [
            r.client_id
            for r in self.store.open_orders()
            if r.algo and r.symbol == symbol and r.purpose == "protect"
        ]
