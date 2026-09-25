"""Validation statistics against known values and constructed cases."""

from __future__ import annotations

import math

import pytest

np = pytest.importorskip("numpy")

from quanta.research.cv import cpcv, cpcv_splits, n_paths, pbo_cscv  # noqa: E402
from quanta.research.resample import (  # noqa: E402
    block_length,
    stationary_bootstrap_idx,
)
from quanta.research.stats import (  # noqa: E402
    dsr,
    effective_trials,
    expected_max_sr,
    moments,
    newey_west_t,
    psr,
)


def test_dsr_matches_the_paper_example() -> None:
    # Bailey & López de Prado (2014): N=100 trials, V[SR]=0.5/250, T=1250 daily returns,
    # annualized SR 2.5, skew −3, kurtosis 10
    v, sr = 0.5 / 250, 2.5 / math.sqrt(250)
    assert expected_max_sr(100, v) == pytest.approx(0.11317, abs=1e-5)
    assert dsr(sr, 1250, -3.0, 10.0, 100, v) == pytest.approx(0.90040, abs=1e-5)


def test_psr_basics() -> None:
    assert psr(0.1, 0.1, 500, 0.0, 3.0) == pytest.approx(0.5)
    assert psr(0.2, 0.0, 500, 0.0, 3.0) > 0.99
    # negative skew and fat tails make the same Sharpe less convincing
    assert psr(0.1, 0.0, 250, -2.0, 12.0) < psr(0.1, 0.0, 250, 0.0, 3.0)
    assert expected_max_sr(1, 0.01) == 0.0


def test_moments_of_a_normal_sample() -> None:
    x = np.random.default_rng(1).normal(0.001, 0.02, 200_000)
    m = moments(x)
    assert m.skew == pytest.approx(0.0, abs=0.02) and m.kurt == pytest.approx(3.0, abs=0.05)


def test_effective_trials() -> None:
    rng = np.random.default_rng(2)
    base = rng.normal(size=(1000, 1))
    same = np.repeat(base, 8, axis=1)
    assert effective_trials(same).n_eff == pytest.approx(1.0)
    indep = rng.normal(size=(5000, 8))
    tc = effective_trials(indep)
    assert tc.n_eff == pytest.approx(8.0, abs=0.3) and tc.participation == pytest.approx(8, abs=0.5)


def test_newey_west_t_accounts_for_autocorrelation() -> None:
    rng = np.random.default_rng(3)
    e = rng.normal(0.02, 1.0, 20_000)
    ma = np.convolve(e, np.ones(10) / 10, mode="valid")  # strongly autocorrelated
    naive = ma.mean() / (ma.std(ddof=1) / math.sqrt(ma.size))
    assert abs(newey_west_t(ma, 20)) < abs(naive) / 2


def test_cpcv_structure() -> None:
    splits = cpcv_splits(600, n_groups=6, k=2, purge=5, embargo=7)
    assert len(splits) == 15 and n_paths(6, 2) == 5
    tested = np.zeros(6, dtype=int)
    for s in splits:
        for g in s.test_groups:
            tested[g] += 1
        assert not set(s.train) & set(s.test)
        # purge before and embargo after each test block
        for g in s.test_groups:
            lo, hi = g * 100, (g + 1) * 100
            assert not set(range(max(0, lo - 5), lo)) & set(s.train)
            assert not set(range(hi, min(600, hi + 7))) & set(s.train)
    assert list(tested) == [5] * 6


def test_cpcv_paths_cover_every_period_once() -> None:
    rng = np.random.default_rng(4)
    r = rng.normal(size=(600, 4))
    r[:, 2] += 0.3  # a clearly better parameter set
    res = cpcv(r, 6, 2)
    assert res.paths.shape == (5, 600)
    assert all(sel == 2 for sel in res.selected)
    assert np.allclose(res.paths, r[:, 2])


def test_pbo_extremes() -> None:
    # no skill: the in-sample best is a coin flip out of sample (PBO ≈ 0.5 on average; a
    # single sample varies a lot because the 12 870 splits are highly dependent)
    pbos = [pbo_cscv(np.random.default_rng(s).normal(size=(800, 10)), 16).pbo for s in range(30)]
    assert 0.4 < float(np.mean(pbos)) < 0.6
    rng = np.random.default_rng(5)
    noise = rng.normal(size=(1600, 10))
    planted = noise.copy()
    planted[:, 3] += 0.25
    assert pbo_cscv(planted, 16).pbo < 0.05
    # in-sample winners are out-of-sample losers: each trial is good in half the blocks
    anti = rng.normal(0, 0.1, size=(1600, 2))
    for b in range(16):
        anti[b * 100 : (b + 1) * 100, b % 2] += 1.0
    # unbalanced splits (62 %) pick the out-of-sample loser; balanced ones (C(8,4)²/C(16,8)
    # = 38 %) are coin flips → PBO ≈ 0.62 + 0.38/2 ≈ 0.81
    assert pbo_cscv(anti, 16).pbo == pytest.approx(0.81, abs=0.05)


def test_stationary_bootstrap() -> None:
    rng = np.random.default_rng(6)
    idx = stationary_bootstrap_idx(1000, 10.0, 200, rng)
    assert idx.shape == (200, 1000) and idx.min() >= 0 and idx.max() < 1000
    breaks = (np.diff(idx, axis=1) % 1000 != 1).mean()
    assert breaks == pytest.approx(0.1, abs=0.01)  # mean block length ≈ 10
    iid = stationary_bootstrap_idx(1000, 1.0, 50, np.random.default_rng(7))
    assert (np.diff(iid, axis=1) % 1000 != 1).mean() > 0.99
    again = stationary_bootstrap_idx(1000, 10.0, 200, np.random.default_rng(6))
    assert (again == idx).all()


def test_block_length_grows_with_persistence() -> None:
    rng = np.random.default_rng(8)
    white = rng.normal(size=4000)
    ar = np.zeros(4000)
    for i in range(1, 4000):
        ar[i] = 0.8 * ar[i - 1] + white[i]
    assert block_length(white) < 3.0 < block_length(ar)
