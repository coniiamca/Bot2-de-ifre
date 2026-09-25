"""The research pipeline itself: noise must not pass, a planted effect must."""

from __future__ import annotations

import math

import pytest

np = pytest.importorskip("numpy")

from quanta.research.backtest import simulate  # noqa: E402
from quanta.research.costs import CostModel  # noqa: E402
from quanta.research.gates import alpha_vs, family_stats, plateau  # noqa: E402
from quanta.research.stats import dsr, effective_trials, moments, sharpe  # noqa: E402
from quanta.research.strategies import TrendParams, trend_weights  # noqa: E402
from quanta.research.synthetic import make_market  # noqa: E402


def test_deflation_controls_false_discoveries_on_noise() -> None:
    """16 correlated zero-skill trials, 200 worlds: the naive best-trial test "discovers"
    an edge often; DSR ≥ 0.95 about as rarely as its 5 % level promises."""
    naive = deflated = 0
    worlds = 200
    for seed in range(worlds):
        rng = np.random.default_rng(seed)
        common = rng.normal(size=(1500, 1))
        r = 0.01 * (0.6 * common + 0.8 * rng.normal(size=(1500, 16)))
        srs = [sharpe(r[:, i]) for i in range(16)]
        b = int(np.argmax(srs))
        t_stat = srs[b] * math.sqrt(1500)
        naive += t_stat > 1.645
        m = moments(r[:, b])
        d = dsr(srs[b], 1500, m.skew, m.kurt, effective_trials(r).n_eff, float(np.var(srs, ddof=1)))
        deflated += d >= 0.95
    assert naive / worlds > 0.25
    assert deflated / worlds <= 0.08


GRID = [TrendParams(lb, 24, sig) for lb in (72, 168) for sig in ("sign", "scaled")]


def _family(beta: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    m = make_market(6, 24 * 365 * 3, seed=seed, tsmom_beta=beta)
    cols = []
    for p in GRID:
        res = simulate(trend_weights(m, p), m, CostModel())
        cols.append(res.daily(m.grid)[1])
    bench = simulate(trend_weights(m, TrendParams(168, 24, "sign", long_only=True)), m, CostModel())
    return np.column_stack(cols), bench.daily(m.grid)[1]


@pytest.mark.slow
def test_planted_trend_passes_and_noise_fails() -> None:
    planted, bench = _family(0.035, seed=11)
    fs = family_stats(planted, [str(p) for p in GRID], hold_days=7, lookback_days=7)
    gates = {g.name: g.passed for g in fs.gates}
    assert gates["dsr"] and gates["cpcv"], fs.gates
    a = alpha_vs(planted[:, fs.best], bench, 7)
    assert a.alpha_annual > 0 and a.t_nw > 2
    assert plateau(fs.sr_annual, fs.best, [i for i in range(4) if i != fs.best]).passed

    noise, _ = _family(0.0, seed=12)
    fs0 = family_stats(noise, [str(p) for p in GRID], hold_days=7, lookback_days=7)
    assert not fs0.passed
    assert fs0.dsr < 0.95


def test_prior_trials_raise_the_luck_bar_and_never_change_selection() -> None:
    rng = np.random.default_rng(5)
    t = 1500
    edge = 0.0012 + 0.01 * rng.normal(size=(t, 1))  # a real but modest edge
    r = edge + 0.004 * rng.normal(size=(t, 4))
    names = [f"c{i}" for i in range(4)]
    alone = family_stats(r, names, hold_days=1, lookback_days=1)
    assert family_stats(r, names, 1, 1, prior=None).dsr == alone.dsr  # unchanged without prior
    prior = 0.01 * rng.normal(size=(t, 16))  # 16 earlier, unrelated trials
    both = family_stats(r, names, hold_days=1, lookback_days=1, prior=prior)
    assert both.n_prior == 16 and both.n_eff > alone.n_eff
    assert both.dsr < alone.dsr
    # selection, t, PBO and CPCV stay within this grid
    assert both.best == alone.best and both.best_t_nw == alone.best_t_nw
    assert both.pbo == alone.pbo and both.cpcv_path_sr_annual == alone.cpcv_path_sr_annual
    with pytest.raises(ValueError, match="same days"):
        family_stats(r, names, 1, 1, prior=prior[:-1])


def test_counted_prior_trials_only_lower_the_dsr() -> None:
    rng = np.random.default_rng(6)
    t = 1500
    edge = 0.0012 + 0.01 * rng.normal(size=(t, 1))
    r = edge + 0.004 * rng.normal(size=(t, 4))
    names = [f"c{i}" for i in range(4)]
    alone = family_stats(r, names, hold_days=1, lookback_days=1)
    assert family_stats(r, names, 1, 1, prior_count=0).dsr == alone.dsr
    counted = family_stats(r, names, hold_days=1, lookback_days=1, prior_count=233)
    assert counted.n_prior == 233 and counted.n_eff == alone.n_eff + 233
    assert counted.dsr <= alone.dsr
    assert counted.best == alone.best and counted.pbo == alone.pbo
    # identical trials: the grid's own Sharpe spread is 0, the 1/T noise floor still applies
    same = np.repeat(r[:, :1], 4, axis=1)
    floor = family_stats(same, names, 1, 1, prior_count=100)
    assert floor.dsr < family_stats(same, names, 1, 1).dsr
