"""The bot's loops: a decision every 5 minutes, each trade's life (entry → stop / target /
time exit), the journal of closed trades and a status line on the console.

A trade:

    entry   post-only limit resting ``entry_valid`` → filled: stop (+ target) at the
            exchange → open; not filled: cancelled, no trade
    open    flat (stop or target hit) → done; ``hold`` elapsed → time exit
    exit    post-only limit at the touch for ``exit_valid`` → else market → done

Trades in progress are saved (``trades.json``) so a restart resumes them; a position the bot
finds without a trade of its own is closed. Ctrl+C cancels entries and, with
``close_on_exit``, closes the bot's positions (otherwise their stops stay at the exchange).
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanta.bot.broker import Broker, Fill, OrderInfo
from quanta.bot.config import BotConfig
from quanta.bot.model import ModelBundle
from quanta.bot.strategy import (
    Signal,
    entry_price,
    exit_price,
    side_name,
    size,
    stop_price,
    target_price,
    usdc_symbol,
)
from quanta.core.clock import Clock, LiveClock
from quanta.core.log import get_logger
from quanta.oms.state import OrderStatus

log = get_logger(__name__)
S = 10**9
D0 = Decimal(0)
SignalSource = Callable[[int], Awaitable[list[Signal]]]
JOURNAL_HEADER = [
    "acilis",
    "kapanis",
    "coin",
    "yon",
    "miktar",
    "giris",
    "cikis",
    "sebep",
    "kar_zarar",
    "ucret",
    "risk",
    "R",
    "tahmin",
]


@dataclass(slots=True)
class Timing:
    grid_s: float = 300.0  # decisions on the 5-minute grid
    delay_s: float = 2.0  # after the bar closes
    hold_s: float = 3600.0
    entry_s: float = 300.0
    exit_s: float = 300.0
    loop_s: float = 1.0
    status_s: float = 60.0


@dataclass(slots=True)
class Trade:
    key: str
    symbol: str
    side: int
    qty: str  # planned (Decimal as text: JSON-safe)
    stop_dist: float
    tp_dist: float
    pred: float
    risk_usd: float
    created_ns: int
    state: str = "entry"  # entry | open | exit | done
    entry_oid: str | None = None
    entry_deadline_ns: int = 0
    entry_px: float = 0.0
    opened_ns: int = 0
    stop_oid: str | None = None
    tp_oid: str | None = None
    exit_oid: str | None = None
    exit_deadline_ns: int = 0
    closes: int = 0  # market close attempts
    reason: str = ""

    @property
    def side_tr(self) -> str:
        return "LONG" if self.side > 0 else "SHORT"


def _ts(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _tr(text: str) -> str:
    """1,234.56 → 1.234,56 (Turkish separators)."""
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def _money(x: float) -> str:
    return _tr(f"{x:+,.2f}")


class Bot:
    def __init__(
        self,
        cfg: BotConfig,
        broker: Broker,
        signals: SignalSource,
        horizon_min: int,
        clock: Clock | None = None,
        timing: Timing | None = None,
        bundle: ModelBundle | None = None,
        out: Callable[[str], None] = print,
    ) -> None:
        self.cfg = cfg
        self.broker = broker
        self.signals = signals
        self.clock = clock or LiveClock()
        st = cfg.strategy
        self.timing = timing or Timing(
            hold_s=horizon_min * 60.0,
            entry_s=st.entry_valid_min * 60.0,
            exit_s=st.exit_valid_min * 60.0,
        )
        self.horizon = horizon_min
        self.bundle = bundle
        self.out = out
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        self.trades_file = cfg.state_dir / "trades.json"
        self.journal_file = cfg.data_dir / f"islemler-{cfg.mode}.csv"
        self.trades: dict[str, Trade] = self._load()
        self.blocked_symbols: dict[str, str] = {}  # symbol → why no entries (e.g. fee)
        self.last_decision = ""
        self.n = 0
        self._stopping = False
        self._orphans_checked_ns = 0

    # -- persistence -------------------------------------------------------------------------
    def _load(self) -> dict[str, Trade]:
        if self.cfg.mode == "paper" or not self.trades_file.exists():
            return {}  # a paper broker starts empty: nothing to resume
        try:
            rows = json.loads(self.trades_file.read_text(encoding="utf-8"))
            return {r["symbol"]: Trade(**r) for r in rows if r.get("state") != "done"}
        except (OSError, ValueError, TypeError) as exc:
            log.warning("bot_trades_unreadable", error=str(exc))
            return {}

    def _save(self) -> None:
        tmp = self.trades_file.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(t) for t in self.trades.values()]), encoding="utf-8")
        tmp.replace(self.trades_file)

    def _journal(self, t: Trade, fills: list[Fill]) -> dict[str, Any]:
        pnl = float(sum(f.realized for f in fills) - sum(f.fee for f in fills))
        fee = float(sum(f.fee for f in fills))
        exit_side = "SELL" if t.side > 0 else "BUY"
        out_q = sum((f.qty for f in fills if f.side == exit_side), D0)
        out_px = (
            float(sum(f.qty * f.price for f in fills if f.side == exit_side) / out_q)
            if out_q
            else 0.0
        )
        in_q = sum((f.qty for f in fills if f.side == side_name(t.side)), D0)
        row = {
            "acilis": _ts(t.opened_ns or t.created_ns),
            "kapanis": _ts(self.clock.now_ns()),
            "coin": t.symbol,
            "yon": t.side_tr,
            "miktar": str(in_q),
            "giris": f"{t.entry_px:.6g}",
            "cikis": f"{out_px:.6g}",
            "sebep": t.reason,
            "kar_zarar": f"{pnl:.4f}",
            "ucret": f"{fee:.4f}",
            "risk": f"{t.risk_usd:.4f}",
            "R": f"{pnl / t.risk_usd:.2f}" if t.risk_usd > 0 else "",
            "tahmin": f"{t.pred:.3f}",
        }
        new = not self.journal_file.exists()
        self.journal_file.parent.mkdir(parents=True, exist_ok=True)
        with self.journal_file.open("a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=JOURNAL_HEADER)
            if new:
                w.writeheader()
            w.writerow(row)
        return row

    # -- decisions ---------------------------------------------------------------------------
    def _open_notional(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for s, q in self.broker.positions().items():
            out[s] = float(abs(q)) * self.broker.view(s).mark
        for s, t in self.trades.items():
            if s not in out:
                out[s] = float(Decimal(t.qty)) * float(t.entry_px or self.broker.view(s).mark)
        return out

    async def open(self, sig: Signal) -> str:
        """Place the entry for a signal; returns what happened (for the console)."""
        if sig.symbol in self.trades:
            return "işlem sürüyor"
        why = self.broker.entry_block() or self.blocked_symbols.get(sig.symbol)
        if why:
            return f"giriş yok: {why}"
        rules = self.broker.rules(sig.symbol)
        v = self.broker.view(sig.symbol)
        if rules is None or v.bid <= 0 or v.ask <= 0:
            return "bu kontrat için kural ya da fiyat yok"
        px = entry_price(sig.side, v.bid, v.ask, rules)
        plan = size(
            sig,
            float(px),
            self.broker.equity,
            self._open_notional(),
            self.horizon,
            self.cfg.strategy,
            self.cfg.sizing,
            rules,
        )
        if isinstance(plan, str):
            return plan
        self.n += 1
        key = f"b{sig.t_ns // S}-{sig.symbol}"
        oid, err = await self.broker.limit(
            sig.symbol, side_name(sig.side), plan.qty, px, f"{key}-e", reduce=False
        )
        if oid is None:
            return f"emir reddedildi: {err}"
        now = self.clock.now_ns()
        self.trades[sig.symbol] = Trade(
            key,
            sig.symbol,
            sig.side,
            str(plan.qty),
            plan.stop_dist,
            plan.tp_dist,
            sig.pred,
            plan.risk_usd,
            now,
            entry_oid=oid,
            entry_deadline_ns=now + int(self.timing.entry_s * S),
            entry_px=float(px),
        )
        self._save()
        return (
            f"{'LONG' if sig.side > 0 else 'SHORT'} limit {plan.qty} @ {px} "
            f"(≈{_tr(f'{plan.notional:,.0f}')} {self.cfg.quote}, "
            f"risk ≈{_tr(f'{plan.risk_usd:,.2f}')})"
        )

    async def decide(self, decision_ns: int) -> None:
        if self.bundle is not None:
            age = self.bundle.age_days(decision_ns)
            if age > self.cfg.strategy.model_grace_days:
                self.last_decision = (
                    f"{_ts(decision_ns)}: model {self.bundle.month} ayı için; yeni ay modeli "
                    "yok → `quanta bot egit` çalıştırın (yeni işlem açılmıyor)"
                )
                self.out(self.last_decision)
                return
        try:
            sigs = await self.signals(decision_ns)
        except Exception as e:
            log.warning("bot_signal_error", error=repr(e))
            self.last_decision = f"{_ts(decision_ns)}: veri hatası ({type(e).__name__})"
            self.out(self.last_decision)
            return
        if not sigs:
            self.last_decision = f"{_ts(decision_ns)}: sinyal yok"
            return
        parts = []
        for sig in sigs:
            res = await self.open(sig)
            parts.append(f"{sig.symbol} {'↑' if sig.side > 0 else '↓'} {res}")
        self.last_decision = f"{_ts(decision_ns)}: " + "; ".join(parts)
        self.out(self.last_decision)

    # -- trade life --------------------------------------------------------------------------
    def _info(self, oid: str | None) -> OrderInfo | None:
        return self.broker.order(oid) if oid else None

    async def _cancel(self, *oids: str | None) -> None:
        for oid in oids:
            info = self._info(oid)
            if oid and info is not None and not info.done:
                with contextlib.suppress(Exception):
                    await self.broker.cancel(oid)

    async def _finish(self, t: Trade, reason: str) -> None:
        await self._cancel(t.entry_oid, t.stop_oid, t.tp_oid, t.exit_oid)
        t.state, t.reason = "done", reason
        await asyncio.sleep(0)  # let fill reports of the closing order land
        since = (t.created_ns // 1_000_000) - 1
        fills = [f for f in self.broker.fills_since(since) if f.symbol == t.symbol]
        if fills:
            row = self._journal(t, fills)
            self.out(
                f"KAPANDI {t.symbol} {t.side_tr} {reason}: {_money(float(row['kar_zarar']))} "
                f"{self.cfg.quote} (R {row['R'] or '-'})"
            )
        del self.trades[t.symbol]
        self._save()

    async def _on_filled(self, t: Trade, info: OrderInfo, pos: Decimal) -> None:
        rules = self.broker.rules(t.symbol)
        assert rules is not None
        t.entry_px = float(info.avg_price) if info.avg_price > 0 else t.entry_px
        t.opened_ns = self.clock.now_ns()
        exit_side = "SELL" if t.side > 0 else "BUY"
        trig = stop_price(t.entry_px, t.side, t.stop_dist, rules)
        t.stop_oid, err = await self.broker.stop(t.symbol, exit_side, trig, f"{t.key}-s")
        if t.stop_oid is None:
            self.out(f"UYARI {t.symbol}: zarar kes konamadı ({err}) → pozisyon kapatılıyor")
            t.state, t.exit_deadline_ns = "exit", 0
            return
        if t.tp_dist > 0:
            tp = target_price(t.entry_px, t.side, t.tp_dist, rules)
            t.tp_oid, err = await self.broker.limit(
                t.symbol, exit_side, abs(pos), tp, f"{t.key}-t", reduce=True
            )
            if t.tp_oid is None:
                log.warning("bot_target_refused", symbol=t.symbol, error=err)
        t.state = "open"
        self.out(
            f"AÇILDI {t.symbol} {t.side_tr} {abs(pos)} @ {t.entry_px:.6g} · zarar kes {trig}"
            + (f" · kâr al {tp}" if t.tp_dist > 0 and t.tp_oid else "")
        )

    async def step(self, t: Trade) -> None:
        now = self.clock.now_ns()
        pos = self.broker.positions().get(t.symbol, D0)
        if t.state == "entry":
            info = self._info(t.entry_oid)
            expired = now >= t.entry_deadline_ns or self._stopping
            if info is not None and not info.done and not expired:
                return
            if info is not None and not info.done:
                await self._cancel(t.entry_oid)
                await asyncio.sleep(0.3)
                info = self._info(t.entry_oid)
                pos = self.broker.positions().get(t.symbol, D0)
            if not pos:
                late = now > t.entry_deadline_ns + 30 * S
                if info is not None and info.filled > 0 and not late:
                    return  # filled, the fill report is still on its way: wait for it
                if info is not None and info.status is OrderStatus.REJECTED:
                    self.out(f"{t.symbol}: giriş emri reddedildi")
                await self._finish(t, "dolmadı")
                return
            await self._on_filled(t, info or OrderInfo(OrderStatus.FILLED, abs(pos), D0), pos)
            self._save()
            return
        if not pos:  # stop, target or an emergency close took it
            tp = self._info(t.tp_oid)
            stop = self._info(t.stop_oid)
            if tp is not None and tp.status is OrderStatus.FILLED:
                reason = "kâr al"
            elif t.state == "exit":
                reason = "süre"
            elif stop is not None and stop.status in (OrderStatus.FILLED, OrderStatus.EXPIRED):
                reason = "zarar kes"
            else:
                reason = "kapandı"
            await self._finish(t, reason)
            return
        rules = self.broker.rules(t.symbol)
        assert rules is not None
        if t.state == "open" and now >= t.opened_ns + int(self.timing.hold_s * S):
            await self._cancel(t.tp_oid)
            t.state = "exit"
            t.exit_deadline_ns = now + int(self.timing.exit_s * S)
            v = self.broker.view(t.symbol)
            px = exit_price(t.side, v.bid, v.ask, v.mid, rules)
            exit_side = "SELL" if t.side > 0 else "BUY"
            t.exit_oid, _ = await self.broker.limit(
                t.symbol, exit_side, abs(pos), px, f"{t.key}-x", reduce=True
            )
            self._save()
            return
        if t.state == "exit" and (now >= t.exit_deadline_ns or t.exit_oid is None):
            await self._cancel(t.exit_oid, t.tp_oid)
            t.closes += 1
            if await self.broker.close(t.symbol, f"{t.key}-m{t.closes}"):
                await self._finish(t, "süre (piyasa emri)" if t.exit_oid else "kapatıldı")
            elif t.closes in (1, 5, 30):
                self.out(f"UYARI {t.symbol}: pozisyon kapatılamadı, tekrar deneniyor")

    async def adopt_orphans(self) -> None:
        """Positions on the bot's contracts that no saved trade explains are closed."""
        for sym, q in self.broker.positions().items():
            if q and sym not in self.trades:
                self.out(f"{sym}: botun bilmediği {q} pozisyon bulundu → kapatılıyor")
                await self.broker.close(sym, f"orphan-{self.clock.now_ns() // S}-{sym}")

    async def manage(self) -> None:
        now = self.clock.now_ns()
        if self.broker.ready and now - self._orphans_checked_ns >= 60 * S:
            await self.adopt_orphans()
            self._orphans_checked_ns = now
        for t in list(self.trades.values()):
            try:
                await self.step(t)
            except Exception as e:
                log.warning("bot_trade_step_error", symbol=t.symbol, error=repr(e))

    # -- console ---------------------------------------------------------------------------
    def status(self) -> str:
        eq = self.broker.equity
        parts = [
            datetime.now().strftime("%H:%M"),
            {"paper": "KAĞIT", "demo": "DEMO", "live": "GERÇEK"}[self.cfg.mode],
            f"bakiye {_tr(f'{eq:,.2f}')} {self.cfg.quote}",
        ]
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        day = self.broker.fills_since(int(today.timestamp() * 1000))
        parts.append(
            f"bugün {_money(float(sum(f.realized for f in day) - sum(f.fee for f in day)))}"
        )
        opened = []
        for t in self.trades.values():
            v = self.broker.view(t.symbol)
            label = {"entry": "giriş bekliyor", "open": "açık", "exit": "çıkışta"}[t.state]
            pnl = ""
            q = self.broker.positions().get(t.symbol, D0)
            if q and v.mark > 0 and t.entry_px:
                pnl = f" {_money(float(q) * (v.mark - t.entry_px))}"
            opened.append(f"{t.symbol} {t.side_tr} {label}{pnl}")
        parts.append("işlem: " + (", ".join(opened) if opened else "yok"))
        block = self.broker.entry_block()
        if block:
            parts.append(f"⚠ {block}")
        return " | ".join(parts)

    # -- loops -------------------------------------------------------------------------------
    async def decision_loop(self, stop: asyncio.Event) -> None:
        grid = int(self.timing.grid_s * S)
        while not stop.is_set():
            now = self.clock.now_ns()
            nxt = (now // grid + 1) * grid
            wait = (nxt - now) / S + self.timing.delay_s
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), wait)
            if stop.is_set():
                break
            await self.decide(nxt)

    async def manage_loop(self, stop: asyncio.Event) -> None:
        last_status = 0
        while not stop.is_set():
            await self.manage()
            now = self.clock.now_ns()
            if now - last_status >= int(self.timing.status_s * S) and self.broker.ready:
                self.out(self.status())
                last_status = now
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), self.timing.loop_s)

    async def fee_loop(self, stop: asyncio.Event) -> None:
        """Daily: the account's maker fee per contract; above ``max_maker_fee`` (the zero-fee
        promotion ended, say) no new trades on that contract."""
        check = getattr(self.broker, "fee_check", None)
        if check is None:
            return
        while not stop.is_set():
            if self.broker.ready:
                symbols = sorted(
                    {s for s in getattr(self.broker, "symbols", [])} | set(self.blocked_symbols)
                )
                fees: dict[str, float] = await check(symbols)
                limit = self.cfg.max_maker_fee
                for sym, fee in fees.items():
                    if fee > limit + 1e-12:
                        if sym not in self.blocked_symbols:
                            self.out(
                                f"UYARI {sym}: maker ücreti %{fee * 100:.3f} (izin verilen "
                                f"%{limit * 100:.3f}) → bu kontratta yeni işlem yok"
                            )
                        self.blocked_symbols[sym] = f"maker ücreti %{fee * 100:.3f}"
                    else:
                        self.blocked_symbols.pop(sym, None)
                wait = 86_400.0 if fees else 60.0
            else:
                wait = 5.0
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), wait)

    async def shutdown(self) -> None:
        """Cancel entries; close positions when configured (stops stay otherwise)."""
        self._stopping = True
        for t in list(self.trades.values()):
            if t.state == "entry":
                await self.step(t)
        if self.cfg.close_on_exit:
            for t in list(self.trades.values()):
                t.state, t.exit_deadline_ns, t.exit_oid = "exit", 0, None
                for _ in range(3):
                    await self.step(t)
                    if t.symbol not in self.trades:
                        break
                    await asyncio.sleep(0.5)
        self._save()

    async def run(self, stop: asyncio.Event) -> None:
        """Until ``stop``; the broker keeps running until the bot has shut down."""
        broker_stop = asyncio.Event()
        broker_task = asyncio.create_task(self.broker.run(broker_stop))
        loops = [
            asyncio.create_task(self.decision_loop(stop)),
            asyncio.create_task(self.manage_loop(stop)),
            asyncio.create_task(self.fee_loop(stop)),
        ]
        try:
            await stop.wait()
        finally:
            stop.set()  # also when cancelled (Ctrl+C on Windows)
            for task in loops:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await asyncio.wait_for(task, 30.0)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.shutdown(), 60.0)
            broker_stop.set()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(broker_task, 15.0)


# ---------------------------------------------------------------- the model as signal source


class ModelSignals:
    """Live features → predictions → signals on the eligible USDC contracts."""

    def __init__(self, cfg: BotConfig, bundle: ModelBundle, data: Any, tradable: set[str]) -> None:
        self.cfg = cfg
        self.bundle = bundle
        self.data = data
        self.tradable = tradable  # USDC contracts the venue lists (TRADING)
        self.last: dict[str, float] = {}

    async def __call__(self, decision_ns: int) -> list[Signal]:
        missing = await self.data.update(decision_ns)
        if missing:
            log.warning("bot_bars_missing", symbols=missing)
        snap = self.data.snapshot(decision_ns)
        if snap is None:
            return []
        rows = snap.rows()
        if not rows:
            return []
        import numpy as np

        names = list(snap.frame.names)
        if names != self.bundle.features:
            raise RuntimeError("model and live features differ (retrain the model)")
        x = np.vstack([r[1] for r in rows]).astype(np.float32)
        preds = self.bundle.predict(x)
        allowed = set(self.cfg.strategy.symbols)
        out: list[Signal] = []
        self.last = {}
        for (usdt, _, sigma, _), p in zip(rows, preds, strict=True):
            self.last[usdt] = float(p)
            side = self.bundle.side(float(p))
            sym = usdc_symbol(usdt, self.cfg.quote)
            if not side or usdt not in self.bundle.usdc or sym not in self.tradable:
                continue
            if allowed and sym not in allowed:
                continue
            out.append(Signal(decision_ns, usdt, sym, side, float(p), sigma))
        return out


def journal_summary(path: Path) -> dict[str, Any]:
    """Totals of a trade journal (``quanta bot durum``)."""
    if not path.exists():
        return {"islem": 0}
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    pnl = [float(r["kar_zarar"]) for r in rows]
    today = datetime.now().strftime("%Y-%m-%d")
    by_reason: dict[str, int] = {}
    for r in rows:
        by_reason[r["sebep"]] = by_reason.get(r["sebep"], 0) + 1
    return {
        "islem": len(rows),
        "toplam": sum(pnl),
        "bugun": sum(p for r, p in zip(rows, pnl, strict=True) if r["kapanis"].startswith(today)),
        "kazanan_orani": sum(p > 0 for p in pnl) / len(pnl) if pnl else 0.0,
        "ortalama": sum(pnl) / len(pnl) if pnl else 0.0,
        "en_iyi": max(pnl, default=0.0),
        "en_kotu": min(pnl, default=0.0),
        "sebepler": by_reason,
        "ilk": rows[0]["acilis"] if rows else "",
        "son": rows[-1]["kapanis"] if rows else "",
        "dosya": os.fspath(path),
    }
