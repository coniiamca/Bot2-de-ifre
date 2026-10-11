"""Indicator strategies: resampling, the indicators against hand-checked values, each rule
fires where it should, signals land on the 1-minute bar that closes the signal bar, and a
planted trend is earned by the trend rules."""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")

from quanta.research.indicators import (  # noqa: E402
    RULES,
    IndicatorParams,
    atr,
    bollinger,
    ema,
    resample,
    rsi,
    rule_side,
    supertrend,
)
from quanta.research.intraday import IntradayCosts, run_trades  # noqa: E402
from quanta.research.minute import MIN_NS, Bars  # noqa: E402

FREE = IntradayCosts(taker_bps=0.0, maker_bps=0.0, slip_bps=(0.0, 0.0, 0.0, 0.0))


def bars(c: np.ndarray) -> Bars:
    n = c.size
    o = np.r_[c[0], c[:-1]]
    h, low = np.maximum(o, c) * 1.0002, np.minimum(o, c) * 0.9998
    v = np.full(n, 10.0)
    t = 1_700_000_000 * 10**9 // (15 * MIN_NS) * (15 * MIN_NS) + np.arange(n) * MIN_NS
    return Bars("SYNUSDC", t.astype(np.int64), o, h, low, c, v, v * c, v / 2,
                np.ones(n, np.int16), np.zeros(0, np.int64), np.zeros(0))  # fmt: skip


def test_resample_builds_complete_bars() -> None:
    c = np.arange(1.0, 31.0)
    b = bars(c)
    b.t = np.delete(b.t, 7)  # one missing minute in the second 5-minute bar
    for f in ("o", "h", "low", "c", "v", "qv", "tb", "rank"):
        setattr(b, f, np.delete(getattr(b, f), 7))
    x = resample(b, 5)
    assert list(x.complete) == [True, False, True, True, True, True]
    assert x.c[0] == 5.0 and x.o[0] == 1.0 and x.close_idx[0] == 4
    assert x.h[2] == pytest.approx(15.0 * 1.0002)


def test_indicator_values() -> None:
    c = np.array(
        [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
         45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64]
    )  # fmt: skip
    r = rsi(c, 14)
    assert np.isnan(r[13]) and 55 < r[14] < 80 and np.all(r[14:] <= 100)
    e = ema(np.array([1.0, 2.0, 3.0]), 3)  # α = 0.5
    assert e[1] == pytest.approx(1.5) and e[2] == pytest.approx(2.25)
    _, mid, up = bollinger(np.arange(1.0, 21.0), 20, 2.0)
    assert mid[-1] == pytest.approx(10.5) and up[-1] - mid[-1] == pytest.approx(2 * np.std(
        np.arange(1.0, 21.0)))  # fmt: skip
    a = atr(np.full(30, 2.0), np.full(30, 1.0), np.full(30, 1.5), 14)
    assert a[-1] == pytest.approx(1.0)
    v = np.r_[np.linspace(100, 80, 200), np.linspace(80, 100, 200)]
    d = supertrend(v * 1.001, v * 0.999, v)
    assert d[150] == -1 and d[-1] == 1


@pytest.mark.parametrize("rule", RULES)
def test_every_rule_fires_both_ways_on_a_wave(rule: str) -> None:
    k = np.arange(4000)
    c = 100.0 * np.exp(0.03 * np.sin(k / 60.0) + 0.0005 * np.sin(k / 3.0))
    c *= np.exp(np.where(k > 2000, 0.00002 * (k - 2000), 0.0))  # a mild trend in the 2nd half
    x = resample(bars(c), 1)
    s = rule_side(rule, x)
    assert (s == 1).any() and (s == -1).any()


def test_signals_sit_on_the_closing_minute_with_equal_bracket() -> None:
    k = np.arange(20_000)
    c = 100.0 * np.exp(0.02 * np.sin(k / 400.0))
    b = bars(c)
    p = IndicatorParams(rule="ema_cross", tf=15)
    sig = p.signals(b)
    assert len(sig) > 5
    assert np.all(b.minute[sig.i] % 15 == 14)  # the 1-minute bar that closes a 15-minute bar
    up = sig.tp - sig.limit
    down = sig.limit - sig.sl
    assert np.allclose(up * sig.side, down * sig.side)  # equal distances
    assert np.all(sig.hold == 12 * 15)


def test_a_planted_trend_is_earned_by_trend_rules() -> None:
    rng = np.random.default_rng(0)
    n = 40 * 1440
    drift = np.repeat(rng.choice([-1.0, 1.0], n // 720), 720) * 0.0002  # 12-hour trends
    c = 100.0 * np.exp(np.cumsum(drift + rng.standard_normal(n) * 0.0005))
    b = bars(c)
    tr = run_trades(b, IndicatorParams(rule="supertrend", tf=15).signals(b), FREE)
    assert len(tr) > 20 and tr.net.mean() > 0.002  # > 20 bps a trade before costs
