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
