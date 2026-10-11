"""The bot's sizing and prices: risk from the stop distance, caps, the liquidation guard,
minimum sizes, post-only prices on the right side of the book."""

from __future__ import annotations

from decimal import Decimal

import pytest

from quanta.bot.config import BotConfig, SizingConfig, StrategyConfig
from quanta.bot.strategy import (
    Plan,
    Signal,
    entry_price,
    exit_price,
    size,
    stop_distance,
    stop_price,
    target_price,
    usdc_symbol,
)
from quanta.venues.binance_usdm.filters import SymbolRules

RULES = SymbolRules(
    "BNBUSDC",
    "TRADING",
    Decimal("0.01"),
    Decimal("0.01"),
    Decimal("0.01"),
    Decimal("10000"),
    Decimal("0.01"),
    Decimal("1000"),
    Decimal("5"),
)
SIGMA = 0.0008  # 1-minute σ → σ·√60 ≈ 0.62 %, stop 3σ√60 ≈ 1.86 %


def sig(side: int = 1, sigma: float = SIGMA) -> Signal:
    return Signal(0, "BNBUSDT", "BNBUSDC", side, 1.0, sigma)


def test_size_follows_the_risk_budget() -> None:
    p = size(sig(), 600.0, 5000.0, {}, 60, StrategyConfig(tp_k=0), SizingConfig(), RULES)
    assert isinstance(p, Plan)
    dist = stop_distance(SIGMA, 60, 3.0)
    assert p.stop_dist == pytest.approx(dist)
    # a stop-out loses 0.5 % of 5,000 $ = 25 $ (minus the quantity rounding)
    assert 24.0 < p.risk_usd <= 25.0
    assert p.notional == pytest.approx(float(p.qty) * 600.0)
    assert p.tp_dist == 0.0


def test_take_profit_distance_when_configured() -> None:
    p = size(sig(), 600.0, 5000.0, {}, 60, StrategyConfig(tp_k=1.5), SizingConfig(), RULES)
    assert isinstance(p, Plan)
    assert p.tp_dist == pytest.approx(p.stop_dist / 2)


def test_caps_per_coin_and_in_total() -> None:
    tiny = 0.00005  # a tiny stop would ask for a huge position
    p = size(sig(sigma=tiny), 600.0, 5000.0, {}, 60, StrategyConfig(), SizingConfig(), RULES)
    assert isinstance(p, Plan) and p.notional <= 5000.0 + 1e-9  # ≤ 1× balance
    held = {"ETHUSDC": 3000.0, "SOLUSDC": 3500.0}  # 1.3× already
    p = size(sig(sigma=tiny), 600.0, 5000.0, held, 60, StrategyConfig(), SizingConfig(), RULES)
    assert isinstance(p, Plan) and p.notional <= 1000.0 + 1e-9  # room to 1.5×


def test_refusals() -> None:
    st, sz = StrategyConfig(), SizingConfig()
    held = {"BNBUSDC": 100.0}
    assert "zaten" in str(size(sig(), 600.0, 5000.0, held, 60, st, sz, RULES))
    three = {"A": 1.0, "B": 1.0, "C": 1.0}
    assert "en fazla" in str(size(sig(), 600.0, 5000.0, three, 60, st, sz, RULES))
    # a 6 % stop at 10× leverage would sit too close to liquidation (~10 %)
    wild = size(sig(sigma=0.0026), 600.0, 5000.0, {}, 60, st, sz, RULES)
    assert "likidasyon" in str(wild)
    # a tiny balance cannot reach the minimum notional (5 USDC)
    assert "küçük" in str(size(sig(), 600.0, 20.0, {}, 60, st, sz, RULES))
    assert isinstance(size(sig(sigma=float("nan")), 600.0, 5000.0, {}, 60, st, sz, RULES), str)


def test_prices_sit_on_the_safe_side() -> None:
    assert entry_price(1, 600.004, 600.02, RULES) == Decimal("600.00")  # join the bid
    assert entry_price(-1, 600.0, 600.011, RULES) == Decimal("600.02")  # join the ask
    assert stop_price(600.0, 1, 0.02, RULES) == Decimal("588.00")
    assert stop_price(600.0, -1, 0.02, RULES) == Decimal("612.00")
    assert target_price(600.0, 1, 0.01, RULES) == Decimal("606.00")
    assert target_price(600.0, -1, 0.01, RULES) == Decimal("594.00")
    # time exit of a long: sell at the ask, not below the last close
    assert exit_price(1, 599.9, 600.0, 600.5, RULES) == Decimal("600.50")
    assert exit_price(-1, 599.9, 600.0, 599.5, RULES) == Decimal("599.50")


def test_symbol_names_and_defaults() -> None:
    assert usdc_symbol("1000PEPEUSDT") == "1000PEPEUSDC"
    cfg = BotConfig()
    assert cfg.mode == "paper" and cfg.sizing.leverage == 10 and cfg.sizing.risk_per_trade == 0.005
    assert cfg.limits.daily_loss_halt == 0.03


def test_config_refuses_inconsistent_limits() -> None:
    with pytest.raises(ValueError, match="max_positions"):
        BotConfig(sizing=SizingConfig(max_positions=5))
    with pytest.raises(ValueError, match="risk_per_trade"):
        SizingConfig(risk_per_trade=0.05)
    with pytest.raises(ValueError, match="leverage"):
        SizingConfig(leverage=50)
