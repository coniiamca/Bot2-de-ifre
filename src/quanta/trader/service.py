"""The trader (design §12–13, Faz 4 slice 1): one process, one account, demo only.

Start-up: **SAFE** (no entries) → server time → symbol rules → account settings (one-way,
isolated, ≤3×) → reconciliation → ACTIVE. Then, concurrently:

* market data (bookTicker + markPrice) and the user stream;
* a 1-second loop that turns stale data, a dropped stream, clock offset, unknown orders and
  reconciliation problems into kill-switch conditions, tracks equity against the daily loss
  limits, keeps a protective stop on every position, renews the dead-man switch while entry
  orders are open, handles operator commands and writes ``state.json`` for the status page;
* the canary (plumbing test cycles) and the drills (``trader.canary``, ``trader.drills``).

Every order goes through :meth:`Trader.place`: risk check first, then the OMS.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections import deque
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import aiohttp

from quanta.core.clock import Clock, LiveClock
from quanta.core.log import get_logger
from quanta.oms.oms import Intent, Oms, SubmitResult
from quanta.oms.state import OPEN, OrderStatus
from quanta.oms.store import Store
from quanta.risk.killswitch import TR, KillSwitch, Level
from quanta.risk.limits import Context, DailyLoss, MarketView, check
from quanta.trader.config import TraderConfig
from quanta.trader.market import MarketFeed
from quanta.venues.binance_usdm.filters import SymbolRules, parse_rules
from quanta.venues.binance_usdm.ratelimit import WeightBudget
from quanta.venues.binance_usdm.signing import Signer
from quanta.venues.binance_usdm.trade_rest import BinanceTradeRest, TradeError
from quanta.venues.binance_usdm.user_stream import UserStream

log = get_logger(__name__)
S = 10**9
PROTECT_GRACE_NS = 10 * S


class Trader:
    def __init__(
        self,
        cfg: TraderConfig,
        session: aiohttp.ClientSession,
        signer: Signer,
        clock: Clock | None = None,
    ) -> None:
        self.cfg = cfg
        self.clock = clock or LiveClock()
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        (cfg.state_dir / "control").mkdir(exist_ok=True)
        self.rest = BinanceTradeRest(
            session, cfg.rest_url, signer, WeightBudget(self.clock, fraction=0.3), self.clock
        )
        self.store = Store(cfg.state_dir / "trader.db")
        self.kill = KillSwitch(self.store, self.clock)
        self.oms = Oms(self.store, self.rest, self.clock, self._problem)
        self.daily = DailyLoss(self.store, cfg.limits)
        self.market = MarketFeed(cfg.ws_url, cfg.symbols, session, self.clock)
        self.user = UserStream(
            self.rest, cfg.ws_url, session, self.clock, self.oms.on_event, self._on_reconnect
        )
        self.rules: dict[str, SymbolRules] = {}
        self.ready = False  # SAFE until the first successful reconciliation
        self.equity = 0.0
        self.clock_offset_s = 0.0
        self.order_times: deque[int] = deque()
        self.position_times: deque[int] = deque()
        self.canary: deque[dict[str, Any]] = deque(maxlen=48)
        self.drills: dict[str, Any] = {}
        self.errors: deque[dict[str, Any]] = deque(maxlen=30)
        self.deadman_hold: set[str] = set()  # symbols whose dead-man switch a drill controls
        self._deadman_on: set[str] = set()
        self._flattening = False
        self._reconcile_needed = True
        self._unprotected_since: dict[str, int] = {}
        self.started_ns = self.clock.now_ns()
        self.ready_ns = 0

    # -- problems → kill switch --------------------------------------------------------------
    def _problem(self, kind: str, detail: str) -> None:
        now = self.clock.now_ns()
        self.errors.append({"ts_ns": now, "kind": kind, "detail": detail[:300]})
        if kind == "auth":
            self.kill.escalate(Level.HALTED, f"API anahtarı reddedildi: {detail}")
        elif kind == "clock":
            self.kill.condition("clock", Level.PAUSED, detail)
        else:  # unknown_order, external_order, position_mismatch, anomaly
            self.kill.condition(kind, Level.PAUSED, detail)
            if kind != "unknown_order":
                self._reconcile_needed = True

    def _on_reconnect(self) -> None:
        self._reconcile_needed = True  # events may have been missed while away

    # -- orders --------------------------------------------------------------------------------
    def context(self) -> Context:
        now = self.clock.now_ns()
        while self.order_times and now - self.order_times[0] > 60 * S:
            self.order_times.popleft()
        while self.position_times and now - self.position_times[0] > 3600 * S:
            self.position_times.popleft()
        marks = {s: self.market.view(s).mark for s in self.cfg.symbols}
        return Context(
            level=self.kill.level if self.ready else max(self.kill.level, Level.PAUSED),
            equity=self.equity,
            positions=self.store.positions(),
            marks=marks,
            orders_last_min=len(self.order_times),
            new_positions_last_hour=len(self.position_times),
            stream_down_s=self.user.down_for_s(now),
            clock_offset_s=self.clock_offset_s,
            frozen=set(self.oms.frozen),
        )

    async def place(self, intent: Intent) -> SubmitResult:
        ctx = self.context()
        mkt = self.market.view(intent.symbol)
        why = check(intent, ctx, mkt, self.cfg.limits)
        if why is not None:
            self.store.journal(
                self.clock.now_ns(),
                "risk_refused",
                purpose=intent.purpose,
                symbol=intent.symbol,
                side=intent.side,
                reason=why,
            )
            return SubmitResult(None, OrderStatus.REJECTED, f"risk: {why}")
        now = self.clock.now_ns()
        self.order_times.append(now)
        if not intent.reduce_only and not intent.algo and not ctx.positions.get(intent.symbol):
            self.position_times.append(now)
        return await self.oms.submit(intent)

    async def wait_status(
        self, client_id: str, done: set[OrderStatus], timeout_s: float
    ) -> OrderStatus:
        deadline = self.clock.now_ns() + int(timeout_s * S)
        while True:
            row = self.store.order(client_id)
            if row and row.status in done:
                return row.status
            if self.clock.now_ns() > deadline:
                return row.status if row else OrderStatus.UNKNOWN
            await asyncio.sleep(0.1)

    async def cancel_everything(self, symbol: str) -> None:
        for row in self.store.open_orders():
            if row.symbol == symbol:
                await self.oms.cancel(row.client_id)
        with contextlib.suppress(TradeError):
            await self.rest.cancel_all_orders(symbol)
        with contextlib.suppress(TradeError):
            await self.rest.cancel_all_algo_orders(symbol)

    async def flatten(self, reason: str, by: str) -> bool:
        """Emergency close: cancel everything, close positions reduce-only (IOC slices, then
        MARKET), verify, then HALTED — only a person resumes (a demo drill excepted)."""
        if self._flattening:
            return False
        self._flattening = True
        try:
            self.kill.set_sticky(Level.FLATTENING, reason, by)
            for sym in self.cfg.symbols:
                await self.cancel_everything(sym)
            for sym in self.cfg.symbols:
                for attempt in range(4):
                    pos = await self._exchange_position(sym)
                    if pos == 0:
                        break
                    side = "SELL" if pos > 0 else "BUY"
                    rules = self.rules[sym]
                    mkt = self.market.view(sym)
                    if attempt < 3 and mkt.bid > 0:
                        band = self.cfg.limits.price_band / 2
                        px = (
                            rules.price(mkt.bid * (1 - band), "down")
                            if side == "SELL"
                            else rules.price(mkt.ask * (1 + band), "up")
                        )
                        intent = Intent(
                            "flatten",
                            sym,
                            side,
                            "LIMIT",
                            rules.qty(abs(pos)),
                            px,
                            "IOC",
                            reduce_only=True,
                        )
                    else:
                        intent = Intent(
                            "flatten",
                            sym,
                            side,
                            "MARKET",
                            rules.qty(abs(pos), market=True),
                            reduce_only=True,
                        )
                    r = await self.oms.submit(intent)  # never blocked by risk: it reduces
                    if r.client_id:
                        await self.wait_status(
                            r.client_id,
                            {
                                OrderStatus.FILLED,
                                OrderStatus.EXPIRED,
                                OrderStatus.CANCELED,
                                OrderStatus.REJECTED,
                            },
                            5.0,
                        )
                    await asyncio.sleep(0.3)
            flat = all([await self._exchange_position(s) == 0 for s in self.cfg.symbols])
            self.kill.set_sticky(Level.HALTED, reason if flat else f"{reason} — KAPATILAMADI", by)
            self.store.journal(self.clock.now_ns(), "flatten_done", flat=flat, reason=reason, by=by)
            self._reconcile_needed = True
            return flat
        finally:
            self._flattening = False

    async def _exchange_position(self, symbol: str) -> Decimal:
        for p in await self.rest.position_risk(symbol):
            return Decimal(p["positionAmt"])
        return Decimal(0)

    async def ensure_protected(self) -> None:
        """Every open position must have a protective stop at the exchange. A position gets
        a grace period (the code that opened it places its own stop right away)."""
        now = self.clock.now_ns()
        positions = self.store.positions()
        for sym in list(self._unprotected_since):
            if not positions.get(sym) or self.oms.open_protective(sym):
                del self._unprotected_since[sym]
        for sym, qty in positions.items():
            if self._flattening or not qty or self.oms.open_protective(sym):
                continue
            first = self._unprotected_since.setdefault(sym, now)
            if now - first < PROTECT_GRACE_NS:
                continue
            if any(
                r.symbol == sym and r.status in (OrderStatus.SENT, OrderStatus.UNKNOWN)
                for r in self.store.unresolved()
            ):
                continue
            mkt = self.market.view(sym)
            if mkt.mark <= 0:
                continue
            pct = self.cfg.canary.stop_pct
            side = "SELL" if qty > 0 else "BUY"
            trig = mkt.mark * (1 - pct) if qty > 0 else mkt.mark * (1 + pct)
            rules = self.rules[sym]
            r = await self.oms.submit(
                Intent(
                    "protect",
                    sym,
                    side,
                    "STOP_MARKET",
                    trigger=rules.price(trig, "down" if qty > 0 else "up"),
                    close_position=True,
                )
            )
            if not r.ok:
                self._problem("unprotected_position", f"{sym}: {r.error}")
                await self.flatten(f"{sym} için zarar kes konamadı", "system")

    # -- periodic work ---------------------------------------------------------------------
    async def startup(self) -> None:
        self.clock_offset_s = await self.rest.sync_time()
        self.rules = parse_rules(await self.rest.exchange_info())
        missing = [s for s in self.cfg.symbols if s not in self.rules]
        if missing:
            raise RuntimeError(f"symbols not listed: {missing}")
        await self.rest.set_one_way()
        for sym in self.cfg.symbols:
            await self.rest.set_isolated(sym)
            await self.rest.set_leverage(sym, self.cfg.leverage)
        await self.refresh_account()
        await self.oms.resolve_unknown()
        await self.reconcile()
        self.ready = True
        self.ready_ns = self.clock.now_ns()
        self.store.journal(self.ready_ns, "ready", level=self.kill.level.name)

    async def refresh_account(self) -> None:
        acct = await self.rest.account()
        self.equity = float(acct.get("totalMarginBalance") or acct.get("totalWalletBalance") or 0)
        level, why = self.daily.update(self.clock.now_ns(), self.equity)
        if level is Level.FLATTENING and self.kill.sticky < Level.HALTED:
            await self.flatten(why, "daily_loss")
        elif level is Level.HALTED:
            self.kill.escalate(Level.HALTED, why)
        self.kill.condition("daily_loss", Level.REDUCING if level is Level.REDUCING else None, why)

    async def reconcile(self) -> None:
        if self.store.unresolved():
            await self.oms.resolve_unknown()
        report = await self.oms.reconcile(self.cfg.symbols)
        if report["ok"]:
            for name in ("external_order", "position_mismatch", "anomaly"):
                self.kill.condition(name, None)
        self._reconcile_needed = False

    def _conditions(self) -> None:
        lim = self.cfg.limits
        now = self.clock.now_ns()
        stale = []
        for sym in self.cfg.symbols:
            v = self.market.view(sym)
            if v.book_age_s > lim.stale_book_s or v.mark_age_s > lim.stale_mark_s:
                stale.append(f"{sym} defter {v.book_age_s:.1f} sn / mark {v.mark_age_s:.1f} sn")
        self.kill.condition("stale_data", Level.PAUSED if stale else None, "; ".join(stale))
        down = self.user.down_for_s(now)
        self.kill.condition(
            "user_stream", Level.PAUSED if down > lim.stale_stream_s else None, f"{down:.0f} sn"
        )
        off = abs(self.clock_offset_s)
        if off > lim.clock_halt_s:
            self.kill.escalate(Level.HALTED, f"saat farkı {off * 1000:.0f} ms")
        self.kill.condition(
            "clock", Level.PAUSED if off > lim.clock_pause_s else None, f"{off * 1000:.0f} ms"
        )
        self.kill.condition(
            "unknown_order",
            Level.PAUSED if self.oms.frozen else None,
            ", ".join(self.oms.frozen.values()),
        )

    async def deadman(self) -> None:
        """While entry orders are open, the exchange cancels them if we stop renewing."""
        entry_syms = {
            r.symbol for r in self.store.open_orders() if not r.algo and r.purpose == "entry"
        }
        for sym in self.cfg.symbols:
            if sym in self.deadman_hold:
                continue
            if sym in entry_syms:
                await self.rest.countdown_cancel_all(sym, self.cfg.deadman_ms)
                self._deadman_on.add(sym)
            elif sym in self._deadman_on:
                await self.rest.countdown_cancel_all(sym, 0)
                self._deadman_on.discard(sym)

    async def commands(self) -> None:
        for f in sorted((self.cfg.state_dir / "control").glob("*.json")):
            try:
                cmd = json.loads(f.read_text())
            except (OSError, ValueError):
                cmd = {}
            f.unlink(missing_ok=True)
            name, by = cmd.get("cmd"), str(cmd.get("by", "operator"))
            self.store.journal(self.clock.now_ns(), "command", cmd=name, by=by)
            if name == "halt":
                self.kill.escalate(Level.HALTED, "operatör: durdur", by)
            elif name == "resume":
                self.kill.resume(by)
            elif name == "flatten":
                await self.flatten("operatör: acil kapat", by)

    def state(self) -> dict[str, Any]:
        now = self.clock.now_ns()
        day_start = self.daily.start or self.equity
        return {
            "ts": datetime.fromtimestamp(now / 1e9, UTC).isoformat(),
            "ts_ns": now,
            "environment": self.cfg.environment,
            "ready": self.ready,
            "level": self.kill.level.name,
            "level_tr": TR[self.kill.level] if self.ready else "GÜVENLİ MOD (açılış)",
            "reasons": self.kill.reasons,
            "equity": self.equity,
            "daily_pnl_pct": (self.equity / day_start - 1.0) * 100 if day_start else 0.0,
            "positions": {s: str(q) for s, q in self.store.positions().items()},
            "open_orders": [
                {
                    "client_id": r.client_id,
                    "symbol": r.symbol,
                    "side": r.side,
                    "type": r.order_type,
                    "purpose": r.purpose,
                    "price": str(r.price),
                    "qty": str(r.qty),
                    "status": r.status.value,
                }
                for r in self.store.orders(OPEN | {OrderStatus.SENT, OrderStatus.UNKNOWN})
            ],
            "market": {s: asdict(self.market.view(s)) for s in self.cfg.symbols},
            "user_stream": {"connected": self.user.connected, "reconnects": self.user.reconnects},
            "clock_offset_ms": round(self.clock_offset_s * 1000, 1),
            "last_ack_ms": self.oms.last_ack_ms,
            "last_reconcile": self.store.get_json("last_reconcile"),
            "canary": list(self.canary)[-12:],
            "drills": self.drills,
            "errors": list(self.errors)[-10:],
            "journal": self.store.recent_journal(20),
        }

    def write_state(self) -> None:
        path = self.cfg.state_dir / "state.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state(), default=str, sort_keys=True))
        os.chmod(tmp, 0o644)
        tmp.replace(path)

    async def loop(self, stop: asyncio.Event) -> None:
        last = {"account": 0, "reconcile": 0, "deadman": 0, "state": 0}
        while not stop.is_set():
            now = self.clock.now_ns()
            try:
                if not self.ready:
                    await self.startup()
                self._conditions()
                if now - last["account"] > self.cfg.account_every_s * S:
                    await self.refresh_account()
                    last["account"] = now
                if self.store.unresolved() or self.oms.frozen:
                    await self.oms.resolve_unknown()
                quiet = not any(r.status is OrderStatus.SENT for r in self.store.unresolved())
                due = now - last["reconcile"] > self.cfg.reconcile_every_s * S
                if quiet and (due or self._reconcile_needed):
                    await self.reconcile()
                    last["reconcile"] = now
                if self.kill.level is Level.FLATTENING and not self._flattening:
                    await self.flatten(self.kill.sticky_reason or "kapatma", "system")
                await self.ensure_protected()
                if now - last["deadman"] > self.cfg.deadman_renew_s * S:
                    await self.deadman()
                    last["deadman"] = now
                await self.commands()
            except TradeError as e:
                self._problem(e.action.value, str(e))
                log.warning("trader_loop_error", error=str(e))
            except Exception as e:
                self.errors.append({"ts_ns": now, "kind": "error", "detail": repr(e)[:300]})
                log.exception("trader_loop_exception")
            if now - last["state"] > 2 * S:
                with contextlib.suppress(OSError):
                    self.write_state()
                last["state"] = now
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), self.cfg.loop_s)

    async def run(self, stop: asyncio.Event) -> None:
        from quanta.trader.canary import canary_loop
        from quanta.trader.drills import drill_loop

        tasks = [
            asyncio.create_task(self.market.run(stop)),
            asyncio.create_task(self.user.run(stop)),
            asyncio.create_task(self.loop(stop)),
        ]
        if self.cfg.canary.enabled:
            tasks.append(asyncio.create_task(canary_loop(self, stop)))
        if self.cfg.drills.enabled:
            tasks.append(asyncio.create_task(drill_loop(self, stop)))
        try:
            await stop.wait()
        finally:
            for t in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await asyncio.wait_for(t, 10.0)
            with contextlib.suppress(OSError):
                self.write_state()
            self.store.close()


def market_ok(v: MarketView) -> bool:
    return v.bid > 0 and v.ask > 0 and v.mark > 0


def write_command(state_dir: Path, cmd: str, by: str) -> Path:
    """Queue an operator command (halt | resume | flatten) for the running trader."""
    if cmd not in ("halt", "resume", "flatten"):
        raise ValueError(cmd)
    d = state_dir / "control"
    d.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    path = d / f"{ts}-{cmd}.json"
    path.write_text(json.dumps({"cmd": cmd, "by": by, "ts": ts}))
    return path
