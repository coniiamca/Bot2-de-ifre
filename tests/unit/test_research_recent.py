"""The video-style recent test: fixed dollar sizing, dollar statistics, outcome classes."""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")

from quanta.research.intraday import Trades, combine  # noqa: E402
from quanta.research.recent_test import Sizing, dollar_stats, outcome  # noqa: E402

DAY = 86400 * 10**9


def trades(n: int, net: float) -> Trades:
    rows = [
        (1, i * DAY // 4, i * DAY // 4 + 3600 * 10**9, 100.0, 100.0, 2, 1, net, 0.0, 0.0, 0.0,
         net, 0.02, False)
        for i in range(n)
    ]  # fmt: skip
    return Trades.from_rows(rows)


def test_every_trade_gets_the_same_dollar_size_up_to_the_position_cap() -> None:
    pf = Sizing(5000.0, 1.0, 2).portfolio()
    tr, w = combine(trades(4, 0.001), pf)
    assert len(tr) == 4 and np.all(w == 1.0)  # a small stop does not grow the size
    # three trades at once: the third is refused (at most 2 open positions)
    t = Trades.from_rows([(1, 0, 10, 1, 1, 2, 1, 0.0, 0, 0, 0, 0.0, 0.02, False)] * 3)
    t.symbol = np.array([0, 1, 2], dtype=np.int32)
    acc, _ = combine(t, pf)
    assert len(acc) == 2


def test_dollar_statistics_on_a_fixed_capital() -> None:
    daily = np.array([0.01, -0.02, 0.03, 0.0])
    tr, w = combine(trades(8, 0.005), Sizing(5000.0, 1.0, 6).portfolio())
    s = dollar_stats(tr, w, daily, 20_000, 5000.0, [150, 1000])
    assert s["avg_day_usd"] == pytest.approx(25.0) and s["total_usd"] == pytest.approx(100.0)
    assert s["worst_day_usd"] == pytest.approx(-100.0) and s["best_day_usd"] == pytest.approx(150)
    assert s["max_drawdown_usd"] == pytest.approx(-100.0)
    assert s["loss_days"] == {"150": 0, "1000": 0}
    assert s["trades_per_day"] == pytest.approx(2.0) and s["avg_trade_usd"] == pytest.approx(25.0)


def test_outcome_classes() -> None:
    fixed = {"target_usd_day": 200, "max_drawdown_usd": 2500, "min_trades_day": 20}
    good = {"avg_day_usd": 250, "max_drawdown_usd": -2000, "trades_per_day": 25, "t_nw": 3}
    assert outcome(good, fixed) == "HEDEF"
    assert outcome(good | {"trades_per_day": 10}, fixed) == "KARLI"
    assert outcome(good | {"max_drawdown_usd": -3000}, fixed) == "KARLI"
    assert outcome(good | {"avg_day_usd": 20, "t_nw": 1.2}, fixed) == "KANITSIZ"
    assert outcome(good | {"avg_day_usd": -5}, fixed) == "KANITSIZ"


def test_risk_sizing_follows_the_stop_distance() -> None:
    fixed = {"risk_per_trade": 0.005, "cap_symbol": 1.0, "cap_gross": 1.5, "max_positions": 3}
    sz = Sizing.of(fixed | {"capital_usd": 5000.0})
    assert sz.risk_per_trade == 0.005
    pf = sz.portfolio(fixed)
    assert (pf.risk_per_trade, pf.cap_symbol, pf.cap_gross, pf.max_positions) == (
        0.005,
        1.0,
        1.5,
        3,
    )
    # risk 0.5 % with a 2 % stop (the helper's) → a quarter of the capital, under the 1× cap
    _, w = combine(trades(1, 0.01), pf)
    assert w[0] == pytest.approx(0.25)


def test_execution_of_a_setting() -> None:
    from quanta.research.recent_test import execution

    assert execution({"horizon": 60, "q": 0.0025}) == (0.0, "maker", False)
    assert execution({"tp_k": 1.5, "entry": "market", "zero_cost": True}) == (1.5, "market", True)
    with pytest.raises(ValueError, match="entry"):
        execution({"entry": "chase"})
