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
