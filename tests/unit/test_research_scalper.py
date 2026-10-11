"""The quiet-market scalper on synthetic markets: a planted minute-scale mean reversion is
earned (before costs), a pure random walk is not; the two-sided entry takes the first side
traded through, pessimistically when one bar crosses both; the quiet filter removes trends."""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")

from quanta.research.intraday import IntradayCosts, run_trades  # noqa: E402
from quanta.research.minute import MIN_NS, Bars  # noqa: E402
from quanta.research.scalper import ScalperParams, scalper_signals  # noqa: E402

FREE = IntradayCosts(taker_bps=0.0, maker_bps=0.0, slip_bps=(0.0, 0.0, 0.0, 0.0))
P = ScalperParams(a=1.0, tp=1.0, stop=3.0, hold=30, quiet=False)


def bars(logp: np.ndarray, seed: int = 0) -> Bars:
    rng = np.random.default_rng(seed)
    n = logp.size
    c = 100.0 * np.exp(logp)
    o = np.concatenate([[c[0]], c[:-1]])
    wick = np.abs(rng.standard_normal((2, n))) * 0.0002
    h = np.maximum(o, c) * np.exp(wick[0])
    low = np.minimum(o, c) * np.exp(-wick[1])
    v = np.full(n, 10.0)
    t = 1_700_000_000 * 10**9 // MIN_NS * MIN_NS + np.arange(n, dtype=np.int64) * MIN_NS
    return Bars("SYNUSDC", t, o, h, low, c, v, v * c, v / 2, np.ones(n, np.int16),
                np.zeros(0, np.int64), np.zeros(0))  # fmt: skip


def random_walk(n: int, seed: int) -> np.ndarray:
    return np.cumsum(np.random.default_rng(seed).standard_normal(n) * 0.0008)


def mean_reverting(n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = np.zeros(n)
    eps = rng.standard_normal(n) * 0.0008
    for k in range(1, n):
        x[k] = 0.85 * x[k - 1] + eps[k]  # deviations die out within minutes
    return x + np.cumsum(rng.standard_normal(n) * 0.0001)  # slow drifting level


def per_trade(logp: np.ndarray, p: ScalperParams = P) -> tuple[float, float, int]:
    b = bars(logp)
    tr = run_trades(b, scalper_signals(b, p), FREE)
    return float(tr.net.mean()), float((tr.net > 0).mean()), len(tr)


def test_mean_reversion_is_earned_and_a_random_walk_is_not() -> None:
    n = 30 * 1440
    mr, mr_win, n_mr = per_trade(mean_reverting(n, 1))
    rw, _, n_rw = per_trade(random_walk(n, 2))
    assert n_mr > 300 and n_rw > 300
    assert mr > 0.001 and mr_win > 0.7
    # no edge in a random walk: passive fills come when the price runs through them
    assert rw < mr / 3


def test_two_sided_entry_and_the_pessimistic_tie() -> None:
    n = 3 * 1440 + 40
    logp = np.where(np.arange(n) % 2 == 0, 0.0, 0.0001)  # a tiny zigzag: σ > 0, no crossing
    b = bars(logp)
    b.h[:] = b.c * 1.00001
    b.low[:] = b.c * 0.99999
    i = int(np.flatnonzero(b.minute % 5 == 4)[-3])
    b.low[i + 2] = b.c[i] * 0.99  # the buy side is traded through first
    b.h[i + 3] = b.c[i] * 1.01
    sig = scalper_signals(b, ScalperParams(a=1.0, tp=1.0, stop=3.0, hold=30, quiet=False))
    k = list(sig.i).index(i)
    assert sig.side[k] == 1 and sig.limit[k] < b.c[i]
    assert sig.sl[k] < sig.limit[k] < sig.tp[k]
    b.h[i + 2] = b.c[i] * 1.01  # now one bar crosses both, closing in its upper half
    b.c[i + 2] = b.c[i] * 1.005
    sig = scalper_signals(b, ScalperParams(a=1.0, tp=1.0, stop=3.0, hold=30, quiet=False))
    assert sig.side[list(sig.i).index(i)] == -1  # the short, which the bar then went against


def test_the_quiet_filter_skips_trends() -> None:
    n = 10 * 1440
    trend = np.linspace(0.0, 2.0, n) + random_walk(n, 3) * 0.1  # a steady climb
    b = bars(trend)
    loud = scalper_signals(b, P)
    quiet = scalper_signals(b, ScalperParams(a=1.0, tp=1.0, stop=3.0, hold=30, quiet=True))
    assert len(loud) > 100 and len(quiet) < len(loud) / 10
