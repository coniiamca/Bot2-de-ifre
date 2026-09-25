"""Walk-forward forecasts of model M1: slice boundaries, no use of the test month or later,
determinism, and recovery of a planted signal (with a shuffled-label control)."""

from __future__ import annotations

from dataclasses import replace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("lightgbm")

from quanta.research.features import Frame  # noqa: E402
from quanta.research.minute import MIN_NS  # noqa: E402
from quanta.research.ml import (  # noqa: E402
    DAY_NS,
    ModelSpec,
    entries,
    month_start,
    slices,
    walk_forward,
)

H = 60
MONTHS = ["2021-04", "2021-05"]


def frame(seed: int = 0, beta: float = 0.5) -> Frame:
    rng = np.random.default_rng(seed)
    t0 = month_start("2021-01")
    t1 = month_start("2021-06")
    grid = np.arange(t0, t1, 5 * MIN_NS, dtype=np.int64)
    n_sym = 2
    t = np.repeat(grid, n_sym)
    n = t.size
    x = rng.normal(size=(n, 4)).astype(np.float32)
    x[rng.random(n) < 0.05, 1] = np.nan
    y = (beta * x[:, 0] + rng.normal(size=n)).astype(np.float32)
    return Frame(
        t,
        np.tile(np.arange(n_sym, dtype=np.int32), grid.size),
        np.ones(n, dtype=np.int16),
        np.full(n, 100.0),
        np.full(n, 1e-3),
        x,
        ("a", "b", "c", "d"),
        {H: y},
    )


def spec(kind: str) -> ModelSpec:
    s = ModelSpec(kind, train_days=60, calib_days=10, rounds=20, label_span_min=H + 7)
    params = dict(s.params, min_data_in_leaf=200, num_threads=2)
    return replace(s, params=params)


def test_slices_leave_a_gap_longer_than_any_label() -> None:
    s = spec("ridge")
    r0, r1, c0, c1, s0, s1 = slices("2021-04", s)
    span = s.label_span_min * MIN_NS
    assert r0 < r1 and r1 + span < c0 < c1 and c1 + span < s0 < s1
    assert c1 - c0 == 10 * DAY_NS and r1 - r0 == 60 * DAY_NS
    with pytest.raises(ValueError, match="gap"):
        walk_forward(frame(), H, replace(s, label_span_min=2000), MONTHS, np.ones(1, bool), (0.01,))


@pytest.mark.parametrize("kind", ["ridge", "lgbm"])
def test_the_test_month_and_later_never_change_its_forecast(kind: str) -> None:
    f = frame()
    rows = np.ones(len(f), dtype=bool)
    base = walk_forward(f, H, spec(kind), MONTHS[:1], rows, (0.01,))
    s0 = month_start(MONTHS[0])
    g = frame()
    later = g.t >= s0
    g.y[H][later] = np.float32(9.0)  # labels of the test month and after
    g.x[later] = np.float32(100.0)  # … and their features (would move a scaler)
    other = walk_forward(g, H, spec(kind), MONTHS[:1], rows, (0.01,))
    assert other.thresholds == base.thresholds
    early = f.t < s0
    assert np.array_equal(other.pred[early], base.pred[early], equal_nan=True)
    test = (f.t >= s0) & (f.t < month_start("2021-05"))
    assert np.isfinite(base.pred[test]).all() and np.isnan(base.pred[~test]).all()


@pytest.mark.parametrize("kind", ["ridge", "lgbm"])
def test_planted_signal_is_found_and_shuffled_labels_find_nothing(kind: str) -> None:
    f = frame(seed=1)
    rows = np.ones(len(f), dtype=bool)
    fc = walk_forward(f, H, spec(kind), MONTHS, rows, (0.01,))
    test = np.isfinite(fc.pred)
    assert np.corrcoef(fc.pred[test], f.y[H][test])[0, 1] > 0.3
    assert max(fc.importance, key=fc.importance.get) == "a"  # type: ignore[arg-type]
    # without information the forecasts barely move (their direction is noise, so a
    # correlation says little; their spread is the measure)
    null = walk_forward(f, H, spec(kind), MONTHS, rows, (0.01,), shuffle_seed=0)
    assert np.std(null.pred[test]) < 0.3 * np.std(fc.pred[test])
    again = walk_forward(f, H, spec(kind), MONTHS, rows, (0.01,))
    assert np.array_equal(again.pred, fc.pred, equal_nan=True)  # deterministic


def test_entries_follow_the_month_thresholds_and_the_tradable_rows() -> None:
    f = frame(seed=2)
    rows = np.ones(len(f), dtype=bool)
    fc = walk_forward(f, H, spec("ridge"), MONTHS, rows, (0.05,))
    months = np.where(f.t >= month_start("2021-05"), "2021-05", "2021-04")
    months = np.where(f.t < month_start("2021-04"), "2021-03", months)
    tradable = f.sym == 0
    idx, side = entries(f, fc, 0.05, tradable, months)
    assert idx.size and np.all(f.sym[idx] == 0)
    lo, hi = fc.thresholds["2021-04"][0.05]
    april = months[idx] == "2021-04"
    assert np.all(fc.pred[idx][april & (side > 0)] > hi)
    assert np.all(fc.pred[idx][april & (side < 0)] < lo)
    share = idx.size / np.isfinite(fc.pred[tradable]).sum()
    assert 0.03 < share < 0.2  # about 2 × 5 % of the rows
