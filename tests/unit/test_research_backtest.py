"""Backtest accounting identities, point-in-time alignment and the leakage canary."""

from __future__ import annotations

import math

import pytest

np = pytest.importorskip("numpy")

from quanta.research.backtest import daily_returns, simulate  # noqa: E402
from quanta.research.costs import CostModel  # noqa: E402
from quanta.research.panel import HOUR_NS, Market, asof_align  # noqa: E402
from quanta.research.stats import sharpe  # noqa: E402
from quanta.research.synthetic import make_market  # noqa: E402

FREE = CostModel(fee_bps=0, slip_top2_bps=0, slip_rest_bps=0)


def market(prices: list[list[float]], funding: list[list[float]] | None = None) -> Market:
    px = np.array(prices, dtype=np.float64)
    t, n = px.shape
    grid = np.arange(t, dtype=np.int64) * HOUR_NS
    return Market(
        grid=grid,
        symbols=[f"A{i}" for i in range(n)],
        open=px,
        high=px * 1.001,
        low=px * 0.999,
        vwap=px,
        tradable=np.ones((t, n), dtype=bool),
        universe=np.ones((t, n), dtype=bool),
        rank=np.ones((t, n)),
        funding=np.array(funding, dtype=np.float64) if funding else np.zeros((t, n)),
    )


def equity(res_ret: np.ndarray) -> float:
    return float(np.prod(1.0 + res_ret))


def test_buy_and_hold_without_costs_tracks_the_price() -> None:
    m = market([[100.0], [110.0], [99.0], [120.0]])
    res = simulate(np.ones((4, 1)), m, FREE)
    assert equity(res.ret) == pytest.approx(120.0 / 100.0)


def test_one_round_trip_pays_cost_twice() -> None:
    m = market([[100.0], [105.0], [105.0]])
    c = CostModel(fee_bps=10, slip_top2_bps=0, slip_rest_bps=0)
    res = simulate(np.array([[1.0], [0.0], [0.0]]), m, c)
    # exact: entry cost c, return r on the invested equity, exit cost c on the grown weight
    c_ = 0.001
    r1 = 0.05 - c_
    w_after = 1.05 / (1 + r1)
    expected = (1 + r1) * (1 - c_ * w_after)
    assert equity(res.ret) == pytest.approx(expected, rel=1e-9)


def test_funding_sign_and_timing() -> None:
    f = 0.001
    m = market([[100.0], [100.0], [100.0]], [[0.0], [f], [0.0]])
    long = simulate(np.ones((3, 1)), m, FREE)
    assert equity(long.ret) == pytest.approx(1 - f)
    short = simulate(np.full((3, 1), -0.5), m, FREE)
    assert equity(short.ret) == pytest.approx(1 + 0.5 * f)
    # a position opened exactly at the funding hour does not pay it
    late = simulate(np.array([[0.0], [1.0], [1.0]]), m, FREE)
    assert equity(late.ret) == pytest.approx(1.0)


def test_costs_are_linear_and_columns_independent() -> None:
    rng = np.random.default_rng(0)
    px = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, size=(200, 3)), axis=0))
    m = market(px.tolist())
    w = rng.uniform(-0.3, 0.3, size=(200, 3))
    c1 = simulate(w, m, CostModel(stress=1.0))
    c2 = simulate(w, m, CostModel(stress=2.0))
    assert c2.cost[:50].sum() == pytest.approx(2 * c1.cost[:50].sum(), rel=0.05)
    perm = [2, 0, 1]
    mp = market(px[:, perm].tolist())
    p = simulate(w[:, perm], mp, CostModel())
    assert np.allclose(p.ret, simulate(w, m, CostModel()).ret)
    zero = simulate(np.zeros((200, 3)), m, CostModel())
    assert not zero.ret.any()


def test_missing_next_price_forces_an_exit() -> None:
    m = market([[100.0, 50.0], [101.0, 51.0], [102.0, float("nan")], [103.0, float("nan")]])
    res = simulate(np.full((4, 2), 0.5), m, CostModel())
    assert res.weights[1, 1] == 0.0 and res.cost[1] > 0  # exited before the gap
    assert res.weights[1, 0] > 0


def test_asof_align_never_uses_unavailable_values() -> None:
    grid = np.arange(0, 10, dtype=np.int64) * HOUR_NS
    event = np.array([0, 2, 5], dtype=np.int64) * HOUR_NS
    avail = event + HOUR_NS // 2
    vals, src = asof_align(event, avail, np.array([1.0, 2.0, 3.0]), grid)
    assert list(np.nan_to_num(vals, nan=-1)) == [-1, 1, 1, 2, 2, 2, 3, 3, 3, 3]
    assert (src[src >= 0] <= grid[src >= 0]).all()
    stale, _ = asof_align(event, avail, np.array([1.0, 2.0, 3.0]), grid, max_stale_ns=HOUR_NS)
    assert np.isnan(stale[4]) and stale[3] == 2.0


def test_synthetic_market_is_point_in_time() -> None:
    m = make_market(3, 500, seed=1)
    m.check_point_in_time()
    m.avail["close"][10, 0] = m.grid[10] + 1  # pretend a value came from the future
    with pytest.raises(AssertionError, match="look-ahead"):
        m.check_point_in_time()


def test_leakage_canary_has_power() -> None:
    """A feature holding the *next* bar's return: aligned by availability it is useless;
    aligned by event time (a look-ahead bug) it gives an absurd Sharpe. The backtest would
    expose such a bug."""
    m = make_market(3, 24 * 200, seed=2)
    fut = m.open[1:] / m.open[:-1] - 1.0
    fut = np.vstack([fut, np.zeros((1, 3))])
    honest = np.column_stack(
        [asof_align(m.grid, m.grid + HOUR_NS, fut[:, i], m.grid)[0] for i in range(3)]
    )
    leaky = fut  # value of the bar starting at t, used at t
    srs = {}
    for name, feat in (("honest", honest), ("leaky", leaky)):
        res = simulate(np.nan_to_num(np.sign(feat)) / 3.0, m, FREE)  # hourly flips: no costs
        _, d = daily_returns(m.grid, res.ret)
        srs[name] = sharpe(d) * math.sqrt(365)
    assert abs(srs["honest"]) < 3.0
    assert srs["leaky"] > 20.0
