"""H2 crowding test on synthetic markets with a known answer."""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")

from quanta.research.crowding import (  # noqa: E402
    CrowdingParams,
    _test,
    events,
    pit_percentile,
    stratified_diff,
)
from quanta.research.synthetic import make_market  # noqa: E402

P = CrowdingParams(n_boot=300, window_days=60, min_samples=40)


def test_pit_percentile_uses_only_the_past() -> None:
    x = np.array([1.0, 2.0, 3.0, 0.5, np.nan, 10.0])
    r = pit_percentile(x, window=3, min_obs=2)
    assert np.isnan(r[0]) and r[1] == 1.0 and r[2] == 1.0  # the newest is the highest
    assert r[3] == 0.0 and np.isnan(r[4]) and r[5] == 1.0
    # changing a future value never changes an earlier rank
    y = x.copy()
    y[5] = -10.0
    assert np.array_equal(pit_percentile(y, 3, 2)[:5], r[:5], equal_nan=True)


def test_stratified_difference_removes_confounding() -> None:
    # crowding happens mostly in stratum 1, which has more tails regardless of crowding
    y = np.array([0, 0, 0, 0, 1, 1, 1, 1], dtype=bool)
    crowded = np.array([0, 0, 0, 1, 1, 1, 1, 0], dtype=bool)
    strata = np.array([0, 0, 0, 0, 1, 1, 1, 1])
    naive = y[crowded].mean() - y[~crowded].mean()
    assert naive > 0.4
    assert stratified_diff(y, crowded, strata) == pytest.approx(0.0)


@pytest.mark.slow
def test_planted_crowding_tail_is_found_and_noise_is_not() -> None:
    planted = make_market(2, 24 * 365 * 3, seed=21, tail_effect=0.2)
    ev = events(planted, P)
    res = _test(ev, np.ones(ev.time.size, dtype=bool), P)
    assert res.n_crowded > 100
    assert res.ci[0] > 0, res
    null = make_market(2, 24 * 365 * 3, seed=22, tail_effect=0.0)
    ev0 = events(null, P)
    res0 = _test(ev0, np.ones(ev0.time.size, dtype=bool), P)
    assert res0.ci[0] <= 0.0 <= res0.ci[1] or abs(res0.diff) < 0.02, res0
