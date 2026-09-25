"""Synthetic markets with known truth: the research pipeline must find a planted effect
and must not "find" one in noise (plan §14: the pipeline itself is tested).

Returns are fat-tailed (Student-t) with a common factor, like crypto. Optional plants:

* ``tsmom_beta`` — drift proportional to tanh of the trailing 168 h shock sum (time-series
  momentum);
* ``tail_effect`` — extra probability of a crash within 24 h while a persistent "crowding"
  state is high; crowding also drives premium, open interest and the top-trader ratio.

The output is a :class:`Market` with the same fields and availability rules as real data.
"""

from __future__ import annotations

import numpy as np

from quanta.research.minute import MIN_NS, Bars
from quanta.research.panel import HOUR_NS, Market, asof_align, kline_close_feature

START_NS = 1_577_836_800 * 10**9  # 2020-01-01T00:00Z
MOM_WINDOW = 168


def make_market(
    n_assets: int = 6,
    n_hours: int = 24 * 365 * 2,
    seed: int = 0,
    tsmom_beta: float = 0.0,
    tail_effect: float = 0.0,
    factor: float = 0.5,
    t_df: float = 4.0,
) -> Market:
    rng = np.random.default_rng(seed)
    t_len, n = n_hours, n_assets
    sigma = rng.uniform(0.005, 0.012, size=n)
    scale = np.sqrt((t_df - 2.0) / t_df)  # unit-variance Student-t
    f = rng.standard_t(t_df, size=(t_len, 1)) * scale
    e = rng.standard_t(t_df, size=(t_len, n)) * scale
    eps = np.sqrt(factor) * f + np.sqrt(1.0 - factor) * e
    # planted momentum: drift from the shocks of the previous MOM_WINDOW hours
    cs = np.vstack([np.zeros((1, n)), np.cumsum(eps, axis=0)])
    past = cs[:-1] - np.vstack([np.zeros((MOM_WINDOW, n)), cs[: -MOM_WINDOW - 1]])
    mom = past / np.sqrt(MOM_WINDOW)
    r = tsmom_beta * sigma * np.tanh(mom) + sigma * eps
    # crowding: persistent AR(1) state, N(0, 1) stationary
    phi = 0.995
    crowd = np.zeros((t_len, n))
    shocks = rng.normal(size=(t_len, n)) * np.sqrt(1.0 - phi * phi)
    for t in range(1, t_len):
        crowd[t] = phi * crowd[t - 1] + shocks[t]
    if tail_effect > 0:
        crowded = crowd > 1.2816  # top decile of N(0, 1)
        hit = crowded & (rng.random((t_len, n)) < tail_effect / 24.0)
        r = r - hit * 3.0 * sigma * np.sqrt(24.0)
    r = np.maximum(r, -0.5)
    grid = START_NS + np.arange(t_len, dtype=np.int64) * HOUR_NS
    open_px = 100.0 * np.exp(np.vstack([np.zeros((1, n)), np.cumsum(np.log1p(r), axis=0)]))[:-1]
    close_px = open_px * (1.0 + r)
    wick = np.abs(rng.normal(size=(t_len, n))) * sigma * 0.5
    high = np.maximum(open_px, close_px) * (1.0 + wick)
    low = np.minimum(open_px, close_px) * (1.0 - wick)
    premium = 0.0005 * crowd + rng.normal(0.0001, 0.0002, size=(t_len, n))
    funding = np.zeros((t_len, n))
    hours = (grid // HOUR_NS) % 24
    for h in np.flatnonzero(hours % 8 == 0):
        if h >= 8:
            funding[h] = np.clip(premium[h - 8 : h].mean(axis=0), -0.0075, 0.0075)
    oi = np.exp(0.1 * crowd + np.cumsum(rng.normal(0, 0.002, size=(t_len, n)), axis=0))
    top_ratio = 1.0 + 0.2 * crowd + rng.normal(0, 0.05, size=(t_len, n))
    m = Market(
        grid=grid,
        symbols=[f"S{i}USDT" for i in range(n)],
        open=open_px,
        high=high,
        low=low,
        vwap=(open_px + close_px) / 2.0,
        tradable=np.ones((t_len, n), dtype=bool),
        universe=np.ones((t_len, n), dtype=bool),
        rank=np.tile(np.arange(1, n + 1, dtype=np.float64), (t_len, 1)),
        funding=funding,
    )
    _features(m, grid, close_px, premium, oi, top_ratio)
    return m


def _features(
    m: Market,
    grid: np.ndarray,
    close_px: np.ndarray,
    premium: np.ndarray,
    oi: np.ndarray,
    top_ratio: np.ndarray,
) -> None:
    _, n = m.shape
    cols: dict[str, list[np.ndarray]] = {k: [] for k in ("close", "premium", "oi", "top_ratio")}
    avs: dict[str, list[np.ndarray]] = {k: [] for k in cols}
    ten_min = 600 * 10**9
    for i in range(n):
        for name, (vals, avail) in {
            "close": kline_close_feature(grid, grid, close_px[:, i]),
            "premium": asof_align(grid, grid + HOUR_NS, premium[:, i], grid),
            "oi": asof_align(grid, grid + ten_min, oi[:, i], grid),
            "top_ratio": asof_align(grid, grid + ten_min, top_ratio[:, i], grid),
        }.items():
            cols[name].append(vals)
            avs[name].append(avail)
    for name in cols:
        m.add_feature(name, np.column_stack(cols[name]), np.column_stack(avs[name]))


def make_minute_bars(
    n_days: int = 360,
    seed: int = 0,
    shock_rate: float = 4.0,
    shock_sigmas: float = 8.0,
    revert: float = 0.0,
    sigma: float = 0.0008,
    symbol: str = "SYNUSDT",
    rank: int = 1,
    flow_beta: float = 0.0,
) -> Bars:
    """1-minute bars of a random walk with volume
    spikes. ``shock_rate`` times a day on average a 5-minute move of ``shock_sigmas`` σ·√5
    happens on heavy volume; ``revert`` of it drifts back over the next 30 minutes (0 = the
    move is permanent, i.e. no snap-back effect). ``flow_beta``: the aggressive-buyer
    imbalance of the previous 15 minutes (standardised) adds ``flow_beta`` · σ to every
    minute's return — a planted, persistent flow effect for the model M1 pipeline test."""
    rng = np.random.default_rng(seed)
    n = n_days * 1440
    r = rng.standard_normal(n) * sigma
    share = None  # aggressive buyers' share of each minute's volume
    if flow_beta:  # its own stream: flow_beta = 0 keeps the historical series unchanged
        share = np.random.default_rng([seed, 1]).uniform(0.4, 0.6, n)
        imb = 2.0 * share - 1.0  # U(−0.2, 0.2), sd 0.1155
        kernel = np.ones(15) / 15.0
        mean15 = np.convolve(imb, kernel)[:n]  # minutes t−14 … t
        z = np.zeros(n)
        z[1:] = mean15[:-1] / (0.1155 / np.sqrt(15.0))  # known before minute t
        r += flow_beta * sigma * z
    qv = rng.lognormal(0.0, 0.3, n) * 1e5
    starts = np.flatnonzero(rng.random(n) < shock_rate / 1440)
    starts = starts[(starts > 1440) & (starts < n - 60)]
    for s in starts:
        jump = rng.choice([-1.0, 1.0]) * shock_sigmas * sigma * np.sqrt(5.0)
        r[s : s + 5] += jump / 5.0
        qv[s : s + 5] *= 8.0
        r[s + 5 : s + 35] -= revert * jump / 30.0
    c = 100.0 * np.exp(np.cumsum(r))
    o = np.concatenate([[100.0], c[:-1]])
    wick = np.abs(rng.standard_normal((2, n))) * 0.3 * sigma
    h = np.maximum(o, c) * np.exp(wick[0])
    low = np.minimum(o, c) * np.exp(-wick[1])
    v = qv / c
    tb = v * (share if share is not None else rng.uniform(0.4, 0.6, n))
    t = START_NS + np.arange(n, dtype=np.int64) * MIN_NS
    ft = START_NS + np.arange(0, n_days * 3, dtype=np.int64) * 8 * 3600 * 10**9
    fr = rng.normal(0.0001, 0.0002, ft.size)
    return Bars(symbol, t, o, h, low, c, v, qv, tb, np.full(n, rank, np.int16), ft, fr)
