"""Decisions and position sizing, free of I/O (tested directly).

A trade, as model M1 / R1 tested it:

* **Signal:** every 5 minutes the model predicts each top-10 coin's next-hour move (in units
  of its volatility); a prediction beyond the month's thresholds is a long (above) or a
  short (below) — only on coins whose USDC contract is eligible.
* **Entry:** a post-only limit (maker, 0 fee on USDC contracts) at the best bid for a long
  (best ask for a short), resting ``entry_valid_min`` minutes; never chased.
* **Stop:** ``stop_k`` · σ · √h from the fill, a STOP_MARKET on the mark price at the
  exchange (stays there even if the bot stops).
* **Take-profit (optional):** a post-only reduce-only limit ``tp_k`` · σ · √h away (maker).
* **Time exit:** after ``h`` minutes a post-only limit at the touch for
  ``exit_valid_min`` minutes, then a market order.
* **Size:** the stop distance sets it: a stopped-out trade loses ``risk_per_trade`` of the
  balance; capped per coin and in total; one position per coin.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from quanta.bot.config import SizingConfig, StrategyConfig
from quanta.venues.binance_usdm.filters import SymbolRules


@dataclass(frozen=True, slots=True)
class Signal:
    t_ns: int
    usdt: str  # model symbol (USDT perpetual)
    symbol: str  # traded USDC contract
    side: int  # +1 long, −1 short
    pred: float
    sigma: float  # σ of 1-minute log returns at the decision


@dataclass(frozen=True, slots=True)
class Plan:
    symbol: str
    side: int
    qty: Decimal
    price: Decimal  # entry limit
    stop_dist: float  # fraction of the price
    tp_dist: float  # fraction of the price; 0 = none
    notional: float
    risk_usd: float


def usdc_symbol(usdt: str, quote: str = "USDC") -> str:
    return usdt.removesuffix("USDT") + quote


def stop_distance(sigma: float, horizon: int, k: float) -> float:
    return k * sigma * math.sqrt(horizon)


def side_name(side: int) -> str:
    return "BUY" if side > 0 else "SELL"


def stop_price(entry: float, side: int, dist: float, rules: SymbolRules) -> Decimal:
    """Below a long's entry (rounded down), above a short's (rounded up)."""
    px = entry * (1.0 - side * dist)
    return rules.price(px, "down" if side > 0 else "up")


def target_price(entry: float, side: int, dist: float, rules: SymbolRules) -> Decimal:
    """Above a long's entry (rounded up), below a short's (rounded down)."""
    px = entry * (1.0 + side * dist)
    return rules.price(px, "up" if side > 0 else "down")


def entry_price(side: int, bid: float, ask: float, rules: SymbolRules) -> Decimal:
    """Post-only: join the bid for a buy, the ask for a sell (never crosses the book)."""
    return rules.price(bid, "down") if side > 0 else rules.price(ask, "up")


def exit_price(side: int, bid: float, ask: float, last: float, rules: SymbolRules) -> Decimal:
    """Time exit of a position of ``side``: a post-only order on the other side at the touch,
    not better for the market than the last close (as in the research engine)."""
    if side > 0:  # sell
        return rules.price(max(ask, last), "up")
    return rules.price(min(bid, last), "down")


def size(
    sig: Signal,
    price: float,
    equity: float,
    open_notional: dict[str, float],
    horizon: int,
    st: StrategyConfig,
    sz: SizingConfig,
    rules: SymbolRules,
    max_notional: float = math.inf,
) -> Plan | str:
    """The order for a signal, or why there is none. ``max_notional``: the risk limits' largest
    single order (a larger risk budget is cut to it rather than refused)."""
    if equity <= 0 or price <= 0 or not math.isfinite(sig.sigma) or sig.sigma <= 0:
        return "bakiye ya da fiyat yok"
    if sig.symbol in open_notional:
        return "bu coinde zaten işlem var"
    if len(open_notional) >= sz.max_positions:
        return f"en fazla {sz.max_positions} pozisyon"
    dist = stop_distance(sig.sigma, horizon, st.stop_k)
    if dist >= 0.5 / sz.leverage:
        return (
            f"zarar kes %{dist * 100:.1f} uzakta: {sz.leverage}× kaldıraçta likidasyona çok yakın"
        )
    notional = sz.risk_per_trade * equity / dist
    notional = min(notional, sz.cap_symbol * equity)
    room = sz.cap_gross * equity - sum(open_notional.values())
    notional = min(notional, room, max_notional * 0.99)
    qty = rules.qty(notional / price)
    why = rules.check(qty, price) if qty > 0 else "boyut sıfır"
    if why is not None:
        return f"pozisyon çok küçük ({why}); bakiye ya da risk ayarı yetmiyor"
    real = float(qty) * price
    tp = stop_distance(sig.sigma, horizon, st.tp_k) if st.tp_k > 0 else 0.0
    return Plan(
        sig.symbol,
        sig.side,
        qty,
        Decimal(str(price)),
        dist,
        tp,
        real,
        real * dist,
    )
