"""Pre-registered intraday hypotheses (I1–I4) as signal generators on 1-minute bars.

Every input to a decision on bar ``i`` is known at its close: rolling windows end at ``i``,
volatility and volume baselines are lagged, percentile thresholds use only earlier data, and
the engine trades at the earliest in bar ``i + 1``. Parameters come from the grid of the
pre-registration; the ``fixed`` block supplies the rest (see ``research/prereg/I*.yaml``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from numpy.typing import NDArray

from quanta.research.intraday import Signals, contiguous, ewma, rolling_sum
from quanta.research.minute import MIN_NS, Bars

Floats = NDArray[np.float64]
Ints = NDArray[np.int64]
DAY_MIN = 1440
STRATEGIES = ("snapback", "breakout", "flow", "funding_time")


@dataclass(slots=True)
class Prep:
    """Per-symbol inputs shared by every trial."""

    bars: Bars
    sigma: Floats  # EWMA std of 1-minute log returns at the close of each bar
    qv_mean: Floats  # EWMA quote volume per minute
    first_i: int  # first bar allowed to trade (after the warm-up)
    cache: dict[tuple[Any, ...], Any] = field(default_factory=dict)


def prepare(bars: Bars, fixed: dict[str, Any]) -> Prep:
    hl = float(fixed.get("vol_halflife_min", DAY_MIN))
    logc = np.log(bars.c)
    r = np.full(len(bars), np.nan)
    r[1:] = np.diff(logc)
    r[1:][np.diff(bars.minute) != 1] = np.nan  # no return across a gap
    sigma = np.sqrt(ewma(r * r, hl))
    qv_mean = ewma(bars.qv, hl)
    warm = int(fixed.get("warmup_days", 30)) * DAY_MIN * MIN_NS
    first_i = int(np.searchsorted(bars.t, bars.t[0] + warm)) if len(bars) else 0
    return Prep(bars, sigma, qv_mean, first_i)


def _lag(x: Floats, k: int) -> Floats:
    out = np.full(x.size, np.nan)
    if x.size > k:
        out[k:] = x[:-k]
    return out


def rolling_extreme(x: Floats, n: int, fn: str) -> Floats:
    """Max (``fn="max"``) or min of the last ``n`` values in O(T) (van Herk / Gil-Werman)."""
    op = np.maximum if fn == "max" else np.minimum
    fill = -np.inf if fn == "max" else np.inf
    t = x.size
    pad = (-t) % n
    xp = np.concatenate([x, np.full(pad, fill)]).reshape(-1, n)
    prefix = op.accumulate(xp, axis=1).ravel()
    suffix = op.accumulate(xp[:, ::-1], axis=1)[:, ::-1].ravel()
    out = np.full(t, np.nan)
    if t >= n:
        i = np.arange(n - 1, t)
        out[n - 1 :] = op(suffix[i - n + 1], prefix[i])
    return out


def _pit_quantile_hourly(values: Floats, q: float, window_h: int, min_h: int) -> Floats:
    """Per bar: the ``q`` quantile of the hourly samples of ``values`` from the previous
    ``window_h`` hours (samples strictly before the bar's hour). NaN before ``min_h``."""
    t = values.size
    samples = values[::60].copy()
    ok = np.isfinite(samples)
    if not ok.any():
        return np.full(t, np.nan)
    idx = np.where(ok, np.arange(samples.size), 0)
    samples = samples[np.maximum.accumulate(idx)]  # carry the last finite sample
    samples[: int(np.argmax(ok))] = np.nan
    thr = np.full(samples.size, np.nan)
    if samples.size > window_h:
        win = sliding_window_view(samples[:-1], window_h)  # win[j] = samples[j … j+w−1]
        kth = int(q * (window_h - 1))
        part = np.partition(np.nan_to_num(win, nan=np.inf), kth, axis=1)[:, kth]
        count = np.isfinite(win).sum(axis=1)
        thr[window_h:] = np.where(count >= min_h, part, np.nan)
    return np.repeat(thr, 60)[:t]


def snapback(p: Prep, g: dict[str, Any], fixed: dict[str, Any]) -> Signals:
    """I1: after an extreme k-minute move on heavy volume, trade the partial reversal."""
    b, k = p.bars, int(g["k"])
    logc = np.log(b.c)
    move = logc - _lag(logc, k)
    z = move / (_lag(p.sigma, k) * math.sqrt(k))
    vr = rolling_sum(b.qv, k) / (_lag(p.qv_mean, k) * k)
    cond = contiguous(b, k) & (np.abs(z) >= float(g["z"])) & (vr >= float(fixed["vol_ratio_min"]))
    cond[: p.first_i] = False
    i = np.flatnonzero(cond)
    side = -np.sign(z[i]).astype(np.int8)
    c = b.c[i]
    m = np.abs(c - b.c[i - k])
    tp = c + side * float(fixed["tp_frac"]) * m
    sl = c - side * float(fixed["sl_frac"]) * m
    limit = c if g["entry"] == "limit" else np.full(i.size, np.nan)
    return Signals.build(i, side, limit, sl, tp, int(g["hold"]), int(fixed["limit_valid"]))


def breakout(p: Prep, g: dict[str, Any], fixed: dict[str, Any]) -> Signals:
    """I2: after an unusually tight N-minute range, trade the close outside it."""
    b, n = p.bars, int(g["n"])
    key = ("range", n)
    if key not in p.cache:
        hi = rolling_extreme(b.h, n, "max")
        lo = rolling_extreme(b.low, n, "min")
        comp = (hi - lo) / b.c / (p.sigma * math.sqrt(n))
        comp[~contiguous(b, n - 1)] = np.nan
        p.cache[key] = (hi, lo, comp)
    hi, lo, comp = p.cache[key]
    qkey = ("thr", n, float(g["pct"]))
    if qkey not in p.cache:
        days = int(fixed.get("pct_window_days", 30))
        p.cache[qkey] = _pit_quantile_hourly(comp, float(g["pct"]), days * 24, days * 12)
    thr = p.cache[qkey]
    compressed = comp <= thr  # NaN → False
    idx = np.where(compressed, np.arange(comp.size), -1)
    last = np.maximum.accumulate(idx)
    setup = np.full(comp.size, -1)
    setup[1:] = last[:-1]  # most recent setup strictly before the bar
    armed = (setup >= 0) & (np.arange(comp.size) - setup <= n)
    s = np.where(armed, setup, 0)
    hs, ls = hi[s], lo[s]
    prev = _lag(b.c, 1)
    up = armed & (b.c > hs) & (prev <= hs) & contiguous(b, 1)
    down = armed & (b.c < ls) & (prev >= ls) & contiguous(b, 1)
    cond = up | down
    cond[: p.first_i] = False
    i = np.flatnonzero(cond)
    side = np.where(up[i], 1, -1).astype(np.int8)
    width = hs[i] - ls[i]
    sl = (hs[i] + ls[i]) / 2.0
    tp = b.c[i] + side * float(g["m"]) * width
    hold = int(g["hold_mult"]) * n
    return Signals.build(i, side, np.full(i.size, np.nan), sl, tp, hold)


def flow(p: Prep, g: dict[str, Any], fixed: dict[str, Any]) -> Signals:
    """I3: unusually one-sided aggressive (taker) flow over k minutes → trade with it."""
    b, k = p.bars, int(g["k"])
    key = ("imb", k)
    if key not in p.cache:
        vk = rolling_sum(b.v, k)
        tbk = rolling_sum(b.tb, k)
        imb = np.where(vk > 0, (2.0 * tbk - vk) / np.where(vk > 0, vk, 1.0), np.nan)
        imb[~contiguous(b, k - 1)] = np.nan
        hl = float(fixed.get("vol_halflife_min", DAY_MIN))
        mu = ewma(imb, hl)
        var = ewma(imb * imb, hl) - mu * mu
        z = (imb - _lag(mu, 1)) / np.sqrt(np.maximum(_lag(var, 1), 1e-12))
        p.cache[key] = z
    z = p.cache[key]
    cond = np.abs(z) >= float(g["e"])
    cond[: p.first_i] = False
    i = np.flatnonzero(cond)
    side = np.sign(z[i]).astype(np.int8)
    hold = int(g["hold"])
    c = b.c[i]
    d = float(fixed.get("band_sigma", 1.0)) * p.sigma[i] * math.sqrt(hold) * c
    limit = c if g["entry"] == "limit" else np.full(i.size, np.nan)
    return Signals.build(
        i, side, limit, c - side * d, c + side * d, hold, int(fixed["limit_valid"])
    )


def funding_time(p: Prep, g: dict[str, Any], fixed: dict[str, Any]) -> Signals:
    """I4: when the last settled funding rate is extreme against its own recent history, the
    crowded side tends to close before the next funding: trade against it into the funding."""
    b = p.bars
    ft, rate = b.funding_t, b.funding_rate
    pre = int(g["pre"])
    hold = pre - 1 if g["exit"] == "at" else pre + int(fixed.get("after_min", 30))
    window = int(fixed.get("pct_window_days", 90)) * DAY_MIN * MIN_NS
    min_obs = int(fixed.get("min_funding_obs", 60))
    thr = float(g["thr"])
    rows: list[tuple[int, int]] = []
    for q in range(1, ft.size):
        last_t, last = ft[q - 1], rate[q - 1]  # settled and published before the decision
        lo = int(np.searchsorted(ft, last_t - window, "right"))
        hist = rate[lo:q]
        if hist.size < min_obs:
            continue
        pct = float(np.mean(hist <= last))
        s = -1 if pct >= thr else 1 if pct <= 1.0 - thr else 0
        if s == 0:
            continue
        target = ft[q] - (pre + 1) * MIN_NS  # decision bar: entry opens pre minutes before
        j = int(np.searchsorted(b.t, target))
        if j < len(b) and b.t[j] == target and j >= p.first_i and target > last_t + MIN_NS:
            rows.append((j, s))
    i = np.array([r[0] for r in rows], dtype=np.int64)
    side = np.array([r[1] for r in rows], dtype=np.int8)
    c = b.c[i]
    d = float(fixed.get("stop_sigma", 2.0)) * p.sigma[i] * math.sqrt(max(hold, 1)) * c
    limit = c if g["entry"] == "limit" else np.full(i.size, np.nan)
    tp = np.full(i.size, np.nan)
    return Signals.build(i, side, limit, c - side * d, tp, hold, int(fixed["limit_valid"]))


GENERATORS = {
    "snapback": snapback,
    "breakout": breakout,
    "flow": flow,
    "funding_time": funding_time,
}


def signals(strategy: str, p: Prep, g: dict[str, Any], fixed: dict[str, Any]) -> Signals:
    return GENERATORS[strategy](p, g, fixed)
