"""Pre-trade risk checks and the daily loss limit (design §13.1, §13.4 — limits approved by
the user on 2026-09-24). Every order passes :func:`check` before the OMS may send it; the
answer is ``None`` (allowed) or the reason it was refused.

The user's list of protections maps as follows:

* kill switch → ``KillSwitch`` level (entries only when ACTIVE);
* maximum position → order notional, per-coin exposure, gross exposure, number of positions;
* maximum daily loss → :class:`DailyLoss` (2 % → reduce only, 3 % → flatten + halt,
  10 % below the peak → halt);
* stale data → book / mark / user stream ages, clock offset;
* duplicate orders → intent keys in the OMS + orders per minute;
* unexpected price → price band around the mark, maximum spread;
* exchange disconnect → stream ages here, UNKNOWN orders freeze the symbol in the OMS;
* emergency close / reduce-only → exits are always allowed, ``Trader.flatten``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from quanta.core.config import StrictModel
from quanta.oms.oms import Intent
from quanta.oms.store import Store
from quanta.risk.killswitch import Level


class RiskLimits(StrictModel):
    max_order_notional_usdt: float = 1000.0
    max_symbol_exposure: float = 1.0  # × equity
    max_gross_exposure: float = 1.5  # × equity
    max_positions: int = 3
    max_orders_per_min: int = 30
    max_new_positions_per_hour: int = 6
    price_band: float = 0.01  # a price may be at most 1 % away from the mark
    max_spread: float = 0.002  # (ask − bid) / mid
    stale_book_s: float = 2.0
    stale_mark_s: float = 5.0
    stale_stream_s: float = 10.0
    clock_pause_s: float = 0.25
    clock_halt_s: float = 1.0
    daily_loss_reduce: float = 0.02
    daily_loss_halt: float = 0.03
    drawdown_halt: float = 0.10


@dataclass(slots=True)
class MarketView:
    bid: float = 0.0
    ask: float = 0.0
    mark: float = 0.0
    book_age_s: float = float("inf")
    mark_age_s: float = float("inf")

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2 if self.bid and self.ask else self.mark


@dataclass(slots=True)
class Context:
    level: Level
    equity: float
    positions: dict[str, Decimal]
    marks: dict[str, float]
    orders_last_min: int = 0
    new_positions_last_hour: int = 0
    stream_down_s: float = 0.0
    clock_offset_s: float = 0.0
    frozen: set[str] = field(default_factory=set)


def check(intent: Intent, ctx: Context, mkt: MarketView, lim: RiskLimits) -> str | None:
    opening = (
        not intent.reduce_only
        and not intent.close_position
        and intent.purpose
        in (
            "entry",
            "drill",
        )
    )
    if intent.symbol in ctx.frozen:
        return f"{intent.symbol} donduruldu (sonucu bilinmeyen emir)"
    if opening and ctx.level != Level.ACTIVE:
        return f"kill switch: {ctx.level.name}"
    if intent.purpose != "flatten" and ctx.orders_last_min >= lim.max_orders_per_min:
        return f"emir hızı sınırı ({lim.max_orders_per_min}/dk)"
    pos = ctx.positions.get(intent.symbol, Decimal(0))
    if intent.reduce_only and (pos == 0 or (pos > 0) == (intent.side == "BUY")):
        return "azaltılacak pozisyon yok"
    if opening:
        if mkt.book_age_s > lim.stale_book_s or mkt.mark_age_s > lim.stale_mark_s:
            return (
                f"bayat piyasa verisi (defter {mkt.book_age_s:.1f} sn, "
                f"mark {mkt.mark_age_s:.1f} sn)"
            )
        if ctx.stream_down_s > lim.stale_stream_s:
            return f"kullanıcı akışı {ctx.stream_down_s:.0f} sn kopuk"
        if abs(ctx.clock_offset_s) > lim.clock_pause_s:
            return f"saat farkı {ctx.clock_offset_s * 1000:.0f} ms"
    if mkt.mark <= 0:
        return "mark fiyatı yok"
    ref = float(intent.price or intent.trigger or 0)
    if ref:
        dev = abs(ref / mkt.mark - 1.0)
        band = lim.price_band * (5 if intent.algo else 1)  # stops sit further away
        if dev > band:
            return f"beklenmeyen fiyat: {ref} mark'tan %{dev * 100:.2f} uzak"
    if opening and intent.order_type in ("MARKET", "LIMIT"):
        if mkt.bid <= 0 or mkt.ask <= 0 or (mkt.ask - mkt.bid) / mkt.mid > lim.max_spread:
            return "spread çok geniş"
        side_px = mkt.ask if intent.side == "BUY" else mkt.bid
        if abs(side_px / mkt.mark - 1.0) > lim.price_band:
            return "defter fiyatı mark'tan çok uzak"
    if opening and intent.qty is not None:
        px = float(intent.price or 0) or mkt.mark
        notional = float(intent.qty) * px
        if notional > lim.max_order_notional_usdt:
            return f"emir büyüklüğü {notional:.0f} USDT > {lim.max_order_notional_usdt:.0f}"
        signed = intent.qty if intent.side == "BUY" else -intent.qty
        after = float(abs(pos + signed)) * mkt.mark
        if ctx.equity <= 0 or after > lim.max_symbol_exposure * ctx.equity:
            return "coin başına maruziyet sınırı"
        gross = sum(float(abs(q)) * ctx.marks.get(s, 0.0) for s, q in ctx.positions.items())
        gross += after - float(abs(pos)) * mkt.mark
        if gross > lim.max_gross_exposure * ctx.equity:
            return "toplam kaldıraç sınırı"
        if pos == 0:
            if sum(1 for q in ctx.positions.values() if q) >= lim.max_positions:
                return f"en fazla {lim.max_positions} pozisyon"
            if ctx.new_positions_last_hour >= lim.max_new_positions_per_hour:
                return f"saatte en fazla {lim.max_new_positions_per_hour} yeni pozisyon"
    return None


class DailyLoss:
    """Tracks the equity at the start of each UTC day and the peak; persisted in the store."""

    def __init__(self, store: Store, lim: RiskLimits) -> None:
        self.store = store
        self.lim = lim
        st = store.get_json("daily") or {}
        self.day: str = st.get("day", "")
        self.start: float = float(st.get("start", 0.0))
        self.peak: float = float(st.get("peak", 0.0))

    def update(self, now_ns: int, equity: float) -> tuple[Level | None, str]:
        day = datetime.fromtimestamp(now_ns / 1e9, UTC).date().isoformat()
        if day != self.day or self.start <= 0:
            self.day, self.start = day, equity
        self.peak = max(self.peak, equity)
        self.store.set_json("daily", {"day": self.day, "start": self.start, "peak": self.peak})
        loss = 1.0 - equity / self.start if self.start > 0 else 0.0
        dd = 1.0 - equity / self.peak if self.peak > 0 else 0.0
        if dd >= self.lim.drawdown_halt:
            return Level.HALTED, f"zirveden düşüş %{dd * 100:.1f}"
        if loss >= self.lim.daily_loss_halt:
            return Level.FLATTENING, f"günlük zarar %{loss * 100:.1f}"
        if loss >= self.lim.daily_loss_reduce:
            return Level.REDUCING, f"günlük zarar %{loss * 100:.1f}"
        return None, ""
