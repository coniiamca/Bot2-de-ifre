"""Where the bot's orders go: the exchange (demo or live, through the trader's OMS, risk
checks and kill switch) or a paper simulation on the real market.

Both brokers answer the same questions (:class:`Broker`): balance, positions, the market,
the symbol rules, and place / cancel / query orders. The bot's logic does not know which one
it talks to.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

import aiohttp
import msgspec
from pydantic import field_validator

from quanta.bot.config import BotConfig
from quanta.core.clock import Clock, LiveClock
from quanta.core.log import get_logger
from quanta.net.ws import ManagedWebSocket, WsSettings
from quanta.oms.oms import Intent
from quanta.oms.state import TERMINAL, OrderStatus
from quanta.risk.killswitch import Level
from quanta.risk.limits import MarketView
from quanta.trader.canary import close_position
from quanta.trader.config import CanaryConfig, DrillConfig, KeysConfig, TraderConfig
from quanta.trader.market import MarketFeed
from quanta.trader.service import Trader
from quanta.venues.binance_usdm.endpoints import (
    DEMO,
    PRODUCTION,
    Route,
    agg_trade_stream,
    combined_stream_url,
)
from quanta.venues.binance_usdm.filters import SymbolRules, parse_rules
from quanta.venues.binance_usdm.signing import Signer
from quanta.venues.binance_usdm.trade_rest import TradeError

log = get_logger(__name__)
S = 10**9
D0 = Decimal(0)


@dataclass(frozen=True, slots=True)
class OrderInfo:
    status: OrderStatus
    filled: Decimal
    avg_price: Decimal

    @property
    def done(self) -> bool:
        return self.status in TERMINAL


@dataclass(frozen=True, slots=True)
class Fill:
    symbol: str
    side: str
    qty: Decimal
    price: Decimal
    fee: Decimal
    realized: Decimal
    ts_ms: int


class Broker(Protocol):
    name: str

    @property
    def ready(self) -> bool: ...

    @property
    def equity(self) -> float: ...

    def entry_block(self) -> str | None:
        """Why no new position may be opened now (None: allowed)."""
        ...

    def view(self, symbol: str) -> MarketView: ...

    def rules(self, symbol: str) -> SymbolRules | None: ...

    def positions(self) -> dict[str, Decimal]: ...

    def fills_since(self, ts_ms: int) -> list[Fill]: ...

    async def limit(
        self, symbol: str, side: str, qty: Decimal, price: Decimal, key: str, reduce: bool
    ) -> tuple[str | None, str | None]:
        """Post-only limit (maker); returns (order id, error)."""
        ...

    async def stop(
        self, symbol: str, side: str, trigger: Decimal, key: str
    ) -> tuple[str | None, str | None]:
        """STOP_MARKET closePosition on the mark price."""
        ...

    async def close(self, symbol: str, key: str) -> bool:
        """Reduce-only close at the market; True when flat."""
        ...

    async def cancel(self, order_id: str) -> None: ...

    def order(self, order_id: str) -> OrderInfo | None: ...

    async def run(self, stop: asyncio.Event) -> None: ...


# ---------------------------------------------------------------- exchange (demo / live)


class ExchangeTraderConfig(TraderConfig):
    """The trader's configuration for the bot: demo **or live**, leverage up to 20, margin
    type and quote asset. Only the bot builds it, after the live confirmation."""

    environment: str = "demo"  # type: ignore[assignment]
    margin_type: str = "ISOLATED"
    quote: str = "USDC"

    @field_validator("leverage")
    @classmethod
    def _leverage(cls, v: int) -> int:
        if not 1 <= v <= 20:
            raise ValueError("leverage must be 1–20")
        return v

    @field_validator("rest_url", "ws_url")
    @classmethod
    def _not_production(cls, v: str) -> str:
        return v  # the bot decides (mode + --gercek-para)


class BotTrader(Trader):
    cfg: ExchangeTraderConfig

    def equity_from(self, acct: dict[str, Any]) -> float:
        if getattr(self, "multi_assets", False):
            return float(acct.get("totalMarginBalance") or 0)
        for a in acct.get("assets", []):
            if a.get("asset") == self.cfg.quote:
                return float(a.get("marginBalance") or a.get("walletBalance") or 0)
        return 0.0

    async def configure_symbol(self, symbol: str) -> None:
        if not hasattr(self, "multi_assets"):
            try:
                self.multi_assets = await self.rest.multi_assets_mode()
            except TradeError:
                self.multi_assets = False
        if self.multi_assets and self.cfg.margin_type == "ISOLATED":
            raise RuntimeError(
                "Hesap Multi-Assets modunda; bu modda izole marjin yok. Binance'te Single-Asset "
                "moduna geçin ya da ayarlarda sizing.margin_type: CROSSED yapın."
            )
        try:
            await self.rest.set_margin_type(symbol, self.cfg.margin_type)
        except TradeError as e:
            if e.code == -4048:  # a position or order is open: the type cannot change now
                log.warning("bot_margin_type_unchanged", symbol=symbol, error=e.msg)
            else:
                raise
        await self.rest.set_leverage(symbol, self.cfg.leverage)


def exchange_config(cfg: BotConfig, symbols: list[str]) -> ExchangeTraderConfig:
    env = DEMO if cfg.mode == "demo" else PRODUCTION
    return ExchangeTraderConfig(
        environment=cfg.mode,
        rest_url=env.rest_url,
        ws_url=env.ws_url,
        symbols=symbols,
        leverage=cfg.sizing.leverage,
        keys=KeysConfig(
            type=cfg.keys.type,
            api_key_file=cfg.keys.api_key_file,
            secret_file=cfg.keys.secret_file,
        ),
        state_dir=cfg.state_dir,
        limits=cfg.limits,
        # no canary or drills on a real account; the fallback stop (used only when a
        # position is found without one) sits 3 % away
        canary=CanaryConfig(enabled=False, stop_pct=0.03),
        drills=DrillConfig(enabled=False),
        margin_type=cfg.sizing.margin_type,
        quote=cfg.quote,
    )


class ExchangeBroker:
    def __init__(
        self,
        cfg: ExchangeTraderConfig,
        session: aiohttp.ClientSession,
        signer: Signer,
        clock: Clock | None = None,
    ) -> None:
        self.name = cfg.environment
        self.symbols = list(cfg.symbols)
        self.t = BotTrader(cfg, session, signer, clock)
        self.maker_fee: dict[str, float] = {}
        self.max_maker_fee = 0.0

    @property
    def ready(self) -> bool:
        return self.t.ready

    @property
    def equity(self) -> float:
        return self.t.equity

    def entry_block(self) -> str | None:
        if not self.t.ready:
            return "borsa bağlantısı hazırlanıyor"
        if self.t.kill.level is not Level.ACTIVE:
            return f"koruma: {'; '.join(self.t.kill.reasons) or self.t.kill.level.name}"
        return None

    def view(self, symbol: str) -> MarketView:
        return self.t.market.view(symbol)

    def rules(self, symbol: str) -> SymbolRules | None:
        return self.t.rules.get(symbol)

    def positions(self) -> dict[str, Decimal]:
        return self.t.store.positions()

    def fills_since(self, ts_ms: int) -> list[Fill]:
        return [
            Fill(
                f["symbol"],
                f["side"],
                Decimal(f["qty"]),
                Decimal(f["price"]),
                Decimal(f["fee"]),
                Decimal(f["realized"]),
                int(f["ts_ms"]),
            )
            for f in self.t.store.fills_since(ts_ms)
        ]

    async def limit(
        self, symbol: str, side: str, qty: Decimal, price: Decimal, key: str, reduce: bool
    ) -> tuple[str | None, str | None]:
        purpose = "exit" if reduce else "entry"
        r = await self.t.place(
            Intent(purpose, symbol, side, "LIMIT", qty, price, "GTX", reduce_only=reduce, key=key)
        )
        return (r.client_id if r.ok else None), r.error

    async def stop(
        self, symbol: str, side: str, trigger: Decimal, key: str
    ) -> tuple[str | None, str | None]:
        r = await self.t.place(
            Intent("protect", symbol, side, "STOP_MARKET", trigger=trigger, close_position=True,
                   key=key)
        )  # fmt: skip
        return (r.client_id if r.ok else None), r.error

    async def close(self, symbol: str, key: str) -> bool:
        return await close_position(self.t, symbol, key)

    async def cancel(self, order_id: str) -> None:
        await self.t.oms.cancel(order_id)

    def order(self, order_id: str) -> OrderInfo | None:
        row = self.t.store.order(order_id)
        return OrderInfo(row.status, row.filled, row.avg_price) if row else None

    async def fee_check(self, symbols: list[str]) -> dict[str, float]:
        """The account's maker fee per symbol (refreshed daily by the bot)."""
        for s in symbols:
            with contextlib.suppress(TradeError, KeyError, ValueError):
                r = await self.t.rest.commission_rate(s)
                self.maker_fee[s] = float(r["makerCommissionRate"])
        return dict(self.maker_fee)

    async def run(self, stop: asyncio.Event) -> None:
        await self.t.run(stop)


# ---------------------------------------------------------------- paper


@dataclass(slots=True)
class PaperOrder:
    oid: str
    symbol: str
    side: str
    kind: str  # LIMIT | STOP
    qty: Decimal
    price: Decimal  # limit price or stop trigger
    reduce: bool
    status: OrderStatus = OrderStatus.NEW
    filled: Decimal = D0
    avg: Decimal = D0


@dataclass(slots=True)
class PaperPosition:
    qty: Decimal = D0  # signed
    entry: Decimal = D0


class PaperFeed(MarketFeed):
    """bookTicker + markPrice (as the trader) + aggTrade, for the paper fill rules."""

    def __init__(
        self,
        ws_url: str,
        symbols: list[str],
        session: aiohttp.ClientSession,
        clock: Clock,
        on_trade: Any,
        on_mark: Any,
    ) -> None:
        super().__init__(ws_url, symbols, session, clock)
        self._on_trade = on_trade
        self._on_mark = on_mark
        self.funding: dict[str, tuple[float, int]] = {}  # rate, next funding time (ms)
        self._ws.append(
            ManagedWebSocket(
                "paper-trades",
                combined_stream_url(ws_url, Route.MARKET, [agg_trade_stream(s) for s in symbols]),
                session,
                clock,
                self._frame,
                settings=WsSettings(idle_timeout_s=60.0),
            )
        )

    def _frame(self, conn_id: str, seq: int, recv_ns: int, text: str) -> None:
        super()._frame(conn_id, seq, recv_ns, text)
        msg = msgspec.json.decode(text)
        data = msg.get("data", msg) if isinstance(msg, dict) else None
        if not isinstance(data, dict):
            return
        if data.get("e") == "aggTrade":
            self._on_trade(data["s"], Decimal(data["p"]), int(data["T"]))
        elif data.get("e") == "markPriceUpdate":
            if data.get("r") not in (None, ""):
                self.funding[data["s"]] = (float(data["r"]), int(data.get("T") or 0))
            self._on_mark(data["s"])


class PaperBroker:
    """Orders simulated on the real market, with the research engine's pessimistic rules:

    * a post-only limit that would cross the book is rejected (as Binance does);
    * a resting limit fills only when a trade goes *through* its price (a touch is not
      enough: the queue in front of us is unknown), at its own price, maker fee;
    * a stop triggers on the mark price and fills at the touch (bid for a sell), taker fee;
    * a market close fills at the touch, taker fee;
    * funding is booked at each funding time on open positions.
    """

    name = "paper"

    def __init__(
        self,
        cfg: BotConfig,
        symbols: list[str],
        session: aiohttp.ClientSession,
        clock: Clock | None = None,
        rules: dict[str, SymbolRules] | None = None,
        ws_url: str = PRODUCTION.ws_url,
    ) -> None:
        self.cfg = cfg
        self.clock = clock or LiveClock()
        self.symbols = symbols
        self.balance = Decimal(str(cfg.paper.start_balance))
        self.maker = Decimal(str(cfg.paper.maker_fee))
        self.taker = Decimal(str(cfg.paper.taker_fee))
        self.pos: dict[str, PaperPosition] = {s: PaperPosition() for s in symbols}
        self.orders: dict[str, PaperOrder] = {}
        self.fills: list[Fill] = []
        self.keys: set[str] = set()
        self._ids = itertools.count(1)
        self._rules = rules or {}
        self._session = session
        self.feed = PaperFeed(ws_url, symbols, session, self.clock, self._trade, self._mark)
        self._next_funding: dict[str, int] = {}
        self.day = ""
        self.day_start = float(self.balance)
        self.halted = ""

    # -- state -------------------------------------------------------------------------------
    @property
    def ready(self) -> bool:
        return bool(self._rules) and all(self.view(s).mark > 0 for s in self.symbols)

    @property
    def equity(self) -> float:
        upnl = D0
        for s, p in self.pos.items():
            m = self.view(s).mark
            if p.qty and m > 0:
                upnl += p.qty * (Decimal(str(m)) - p.entry)
        return float(self.balance + upnl)

    def entry_block(self) -> str | None:
        if not self.ready:
            return "piyasa verisi bekleniyor"
        lim = self.cfg.limits
        stale = [s for s in self.symbols if self.view(s).book_age_s > max(lim.stale_book_s, 30)]
        if stale:
            return f"bayat piyasa verisi: {', '.join(stale)}"
        return self.halted or None

    def view(self, symbol: str) -> MarketView:
        return self.feed.view(symbol)

    def rules(self, symbol: str) -> SymbolRules | None:
        return self._rules.get(symbol)

    def positions(self) -> dict[str, Decimal]:
        return {s: p.qty for s, p in self.pos.items() if p.qty}

    def fills_since(self, ts_ms: int) -> list[Fill]:
        return [f for f in self.fills if f.ts_ms >= ts_ms]

    def order(self, order_id: str) -> OrderInfo | None:
        o = self.orders.get(order_id)
        return OrderInfo(o.status, o.filled, o.avg) if o else None

    # -- orders ------------------------------------------------------------------------------
    def _new_id(self) -> str:
        return f"p{next(self._ids)}"

    async def limit(
        self, symbol: str, side: str, qty: Decimal, price: Decimal, key: str, reduce: bool
    ) -> tuple[str | None, str | None]:
        if key in self.keys:
            return None, f"duplicate intent {key}"
        self.keys.add(key)
        v = self.view(symbol)
        if v.bid <= 0:
            return None, "piyasa verisi yok"
        if (side == "BUY" and price >= Decimal(str(v.ask))) or (
            side == "SELL" and price <= Decimal(str(v.bid))
        ):
            return None, "post-only emir defteri geçerdi (-5022)"
        pos = self.pos[symbol].qty
        if reduce and (pos == 0 or (pos > 0) == (side == "BUY")):
            return None, "azaltılacak pozisyon yok"
        o = PaperOrder(self._new_id(), symbol, side, "LIMIT", qty, price, reduce)
        self.orders[o.oid] = o
        return o.oid, None

    async def stop(
        self, symbol: str, side: str, trigger: Decimal, key: str
    ) -> tuple[str | None, str | None]:
        if key in self.keys:
            return None, f"duplicate intent {key}"
        self.keys.add(key)
        o = PaperOrder(self._new_id(), symbol, side, "STOP", D0, trigger, True)
        self.orders[o.oid] = o
        self._mark(symbol)  # already beyond the trigger: fires now
        return o.oid, None

    async def close(self, symbol: str, key: str) -> bool:
        p = self.pos[symbol]
        if p.qty:
            v = self.view(symbol)
            side = "SELL" if p.qty > 0 else "BUY"
            px = v.bid if side == "SELL" else v.ask
            if px <= 0:
                return False
            self._fill(symbol, side, abs(p.qty), Decimal(str(px)), self.taker)
        return not self.pos[symbol].qty

    async def cancel(self, order_id: str) -> None:
        o = self.orders.get(order_id)
        if o and o.status not in TERMINAL:
            o.status = OrderStatus.CANCELED

    # -- simulation ----------------------------------------------------------------------------
    def _fill(self, symbol: str, side: str, qty: Decimal, px: Decimal, fee_rate: Decimal) -> None:
        p = self.pos[symbol]
        signed = qty if side == "BUY" else -qty
        realized = D0
        if p.qty and (p.qty > 0) != (signed > 0):  # reducing
            closed = min(abs(signed), abs(p.qty))
            realized = closed * (px - p.entry) * (1 if p.qty > 0 else -1)
            new = p.qty + signed
            if new and (new > 0) != (p.qty > 0):  # flipped (never by the bot's own orders)
                p.entry = px
            p.qty = new
            if not p.qty:
                p.entry = D0
        else:
            total = abs(p.qty) + qty
            p.entry = (p.entry * abs(p.qty) + px * qty) / total if total else D0
            p.qty += signed
        fee = qty * px * fee_rate
        self.balance += realized - fee
        self.fills.append(
            Fill(symbol, side, qty, px, fee, realized, self.clock.now_ns() // 1_000_000)
        )

    def _trade(self, symbol: str, price: Decimal, ts_ms: int) -> None:
        for o in list(self.orders.values()):
            if o.symbol != symbol or o.kind != "LIMIT" or o.status in TERMINAL:
                continue
            through = (o.side == "BUY" and price < o.price) or (
                o.side == "SELL" and price > o.price
            )
            if not through:
                continue
            qty = o.qty - o.filled
            if o.reduce:
                pos = self.pos[symbol].qty
                qty = min(qty, abs(pos)) if pos and (pos > 0) == (o.side == "SELL") else D0
                if qty <= 0:
                    o.status = OrderStatus.EXPIRED  # reduce-only with nothing left to reduce
                    continue
            self._fill(symbol, o.side, qty, o.price, self.maker)
            o.avg = o.price
            o.filled += qty
            o.status = OrderStatus.FILLED

    def _mark(self, symbol: str) -> None:
        v = self.view(symbol)
        if v.mark <= 0:
            return
        mark = Decimal(str(v.mark))
        for o in list(self.orders.values()):
            if o.symbol != symbol or o.kind != "STOP" or o.status in TERMINAL:
                continue
            hit = (o.side == "SELL" and mark <= o.price) or (o.side == "BUY" and mark >= o.price)
            if not hit:
                continue
            pos = self.pos[symbol].qty
            if pos and (pos > 0) == (o.side == "SELL"):
                px = v.bid if o.side == "SELL" else v.ask
                self._fill(symbol, o.side, abs(pos), Decimal(str(px)), self.taker)
                o.filled, o.avg = abs(pos), Decimal(str(px))
            o.status = OrderStatus.FILLED
        self._funding(symbol, mark)

    def _funding(self, symbol: str, mark: Decimal) -> None:
        rate, nxt = self.feed.funding.get(symbol, (0.0, 0))
        if not nxt:
            return
        due = self._next_funding.setdefault(symbol, nxt)
        if self.clock.now_ns() // 1_000_000 >= due:
            p = self.pos[symbol]
            if p.qty and math.isfinite(rate):
                pay = p.qty * mark * Decimal(str(rate))  # longs pay a positive rate
                self.balance -= pay
                self.fills.append(
                    Fill(symbol, "FUNDING", D0, D0, D0, -pay, self.clock.now_ns() // 1_000_000)
                )
            self._next_funding[symbol] = nxt if nxt > due else due + 8 * 3600 * 1000

    async def load_rules(self, rest_url: str = PRODUCTION.rest_url) -> None:
        async with self._session.get(f"{rest_url}/fapi/v1/exchangeInfo") as r:
            r.raise_for_status()
            info = await r.json()
        self._rules = {s: r for s, r in parse_rules(info).items() if s in self.symbols}

    def daily_guard(self) -> None:
        """Paper version of the daily loss limit: entries stop after −3 % in a UTC day."""
        from datetime import UTC, datetime

        day = datetime.fromtimestamp(self.clock.now_ns() / 1e9, UTC).date().isoformat()
        eq = self.equity
        if day != self.day:
            self.day, self.day_start, self.halted = day, eq, ""
        loss = 1.0 - eq / self.day_start if self.day_start > 0 else 0.0
        if loss >= self.cfg.limits.daily_loss_halt and not self.halted:
            self.halted = f"günlük zarar %{loss * 100:.1f}: bugün yeni işlem yok"

    async def run(self, stop: asyncio.Event) -> None:
        if not self._rules:
            await self.load_rules()
        task = asyncio.create_task(self.feed.run(stop))
        while not stop.is_set():
            self.daily_guard()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), 1.0)
        await task
