"""Model M1 features: nothing after the decision can change a row (leakage canary), labels,
and the availability rules of metrics and funding."""

from __future__ import annotations

import math

import pytest

np = pytest.importorskip("numpy")
pa = pytest.importorskip("pyarrow")

from quanta.research.features import (  # noqa: E402
    FEATURES,
    GRID_MIN,
    METRICS_DELAY_NS,
    SYMBOL_FEATURES,
    Frame,
    Series,
    assemble,
    decision_bars,
    labels,
    metrics_series,
    premium_series,
    symbol_frame,
)
from quanta.research.minute import MIN_NS, Bars  # noqa: E402
from quanta.research.synthetic import make_minute_bars  # noqa: E402


def cut(b: Bars, k: int, t_dec: int) -> Bars:
    """The first ``k`` bars, and only the funding settled by ``t_dec``."""
    f = b.funding_t + MIN_NS <= t_dec
    return Bars(
        b.symbol,
        b.t[:k],
        b.o[:k],
        b.h[:k],
        b.low[:k],
        b.c[:k],
        b.v[:k],
        b.qv[:k],
        b.tb[:k],
        b.rank[:k],
        b.funding_t[f],
        b.funding_rate[f],
        b.n[:k],
    )


def aux(b: Bars, seed: int) -> tuple[Series, Series]:
    rng = np.random.default_rng(seed)
    t5 = b.t[::GRID_MIN]
    prem = Series(t5 + 5 * MIN_NS, {"prem": rng.normal(0, 3e-4, t5.size)})
    vals = {
        k: np.exp(rng.normal(0, 0.1, t5.size).cumsum() * 0.01)
        for k in ("oi", "top_ls", "acc_ls", "taker_ls")
    }
    return prem, Series(t5 + METRICS_DELAY_NS, vals)


def upto(s: Series, t_dec: int) -> Series:
    ok = s.avail <= t_dec
    return Series(s.avail[ok], {k: v[ok] for k, v in s.values.items()})


def with_counts(b: Bars, seed: int) -> Bars:
    b.n = np.random.default_rng(seed).poisson(50, len(b)).astype(np.float64)
    return b


def same(a: object, b: object) -> bool:
    return bool(np.allclose(a, b, rtol=1e-6, atol=1e-9, equal_nan=True))


def test_no_feature_sees_anything_after_the_decision() -> None:
    b = with_counts(make_minute_bars(n_days=8, seed=3), 3)
    prem, met = aux(b, 4)
    full = symbol_frame(b, 0, (15, 60), prem, met)
    rows = decision_bars(b)
    rng = np.random.default_rng(0)
    for i in rng.choice(rows, 25, replace=False):
        t_dec = int(b.t[i] + MIN_NS)
        k = int(np.flatnonzero(rows == i)[0])
        part = symbol_frame(
            cut(b, int(i) + 1, t_dec), 0, (15,), upto(prem, t_dec), upto(met, t_dec), rows[[k]]
        )
        for c, name in enumerate(SYMBOL_FEATURES):
            assert same(part.x[0, c], full.x[k, c]), name
        assert same(part.sigma[0], full.sigma[k])
        assert np.isfinite(full.x[k]).sum() > 0.8 * len(SYMBOL_FEATURES)


def test_cross_sectional_features_use_only_the_same_minute() -> None:
    frames = []
    for s in range(6):
        b = with_counts(make_minute_bars(n_days=5, seed=10 + s), s)
        prem, met = aux(b, 20 + s)
        frames.append(symbol_frame(b, s, (15,), prem, met))
    full = assemble(frames, frames[0])
    t_cut = int(np.quantile(full.t, 0.6))
    trimmed = [f.take(np.flatnonzero(f.t <= t_cut)) for f in frames]
    part = assemble(trimmed, trimmed[0])
    a = np.flatnonzero(full.t == t_cut)
    p = np.flatnonzero(part.t == t_cut)
    assert a.size == 6 and p.size == 6
    for c, name in enumerate(FEATURES):
        assert same(part.x[p, c], full.x[a, c]), name
    # ranks lie in [0, 1]; market features exist once enough coins trade
    cs = full.x[:, FEATURES.index("cs_ret_60")]
    assert np.nanmin(cs) == 0.0 and np.nanmax(cs) == 1.0
    assert np.isfinite(full.x[:, FEATURES.index("mkt_15")]).mean() > 0.9
    # BTC's move is the same for every coin in the minute
    btc = full.x[a, FEATURES.index("btc_15")]
    assert np.all(btc == btc[0])


def test_labels_are_next_open_to_open_over_sigma() -> None:
    b = make_minute_bars(n_days=4, seed=1)
    rows = decision_bars(b)[:50]
    sigma = np.full(rows.size, 0.001)
    y = labels(b, rows, sigma, 15)
    i = rows[7]
    want = math.log(b.o[i + 16] / b.o[i + 1]) / (0.001 * math.sqrt(15))
    assert y[7] == pytest.approx(np.clip(want, -5, 5), rel=1e-5)
    # a missing bar at the exit minute: no label
    keep = np.ones(len(b), dtype=bool)
    keep[i + 16] = False
    g = Bars(
        b.symbol,
        b.t[keep],
        b.o[keep],
        b.h[keep],
        b.low[keep],
        b.c[keep],
        b.v[keep],
        b.qv[keep],
        b.tb[keep],
        b.rank[keep],
        b.funding_t,
        b.funding_rate,
    )
    assert np.isnan(labels(g, np.array([i]), np.array([0.001]), 15)[0])


def test_metrics_are_deduplicated_and_known_six_minutes_late() -> None:
    ct = np.array([0, 0, 5, 10], dtype=np.int64) * MIN_NS + 10**12
    table = pa.table(
        {
            "create_time": pa.array(ct).cast(pa.timestamp("ns", tz="UTC")),
            "sum_open_interest": [1.0, 1.0, 2.0, 3.0],
            "sum_toptrader_long_short_ratio": [1.0, 1.0, 1.1, 1.2],
            "count_long_short_ratio": [1.0, 1.0, 1.0, 1.0],
            "sum_taker_long_short_vol_ratio": [1.0, 1.0, 1.0, 1.0],
        }
    )
    s = metrics_series(table)
    assert s is not None and s.avail.size == 3
    assert list(s.avail) == list(np.unique(ct) + METRICS_DELAY_NS)
    ptable = pa.table(
        {"open_time": pa.array(ct[1:]).cast(pa.timestamp("ns", tz="UTC")), "close": [1, 2, 3]}
    )
    p = premium_series(ptable)
    assert p is not None and p.avail[0] == ct[1] + 5 * MIN_NS


def test_funding_counts_only_once_settled() -> None:
    b = make_minute_bars(n_days=5, seed=2)
    rows = decision_bars(b)
    f = symbol_frame(b, 0, (15,))
    col = SYMBOL_FEATURES.index("funding_8h")
    ft = b.funding_t[12]  # day 4, after the warm-up
    # the decision right before the settlement (+1 min) still shows the previous rate
    k = int(np.searchsorted(f.t, ft + MIN_NS)) - 1
    assert f.t[0] < f.t[k] < ft + MIN_NS
    assert f.x[k, col] == pytest.approx(b.funding_rate[11] * 1e4, rel=1e-5)
    k2 = int(np.searchsorted(f.t, ft + MIN_NS))
    assert f.x[k2, col] == pytest.approx(b.funding_rate[12] * 1e4, rel=1e-5)
    assert rows.size == len(f)


def test_frame_take_keeps_columns_aligned() -> None:
    b = make_minute_bars(n_days=4, seed=5)
    f: Frame = symbol_frame(b, 2, (15, 60))
    idx = np.array([3, 1, 4], dtype=np.int64)
    g = f.take(idx)
    assert np.array_equal(g.t, f.t[idx]) and np.array_equal(g.x, f.x[idx], equal_nan=True)
    assert np.array_equal(g.y[60], f.y[60][idx], equal_nan=True)
