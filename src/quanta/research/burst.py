"""H5 feature: the aggressive-flow imbalance at the opening of each quarter hour.

Kim & Hansen, "The Quarter-Hour Effect" (arXiv:2607.09426, 2026) measure the imbalance in
the first 10 seconds after :00/:15/:30/:45 from aggregate trades and find it predicts the
next 4–12 hours. We approximate it with the first *minute* (1-minute klines carry the taker
buy volume): OI = (2·taker buy − volume) / volume ∈ [−1, 1], known at the minute's close.

For each hour of the research grid the feature is the mean OI of the quarter-hour openings
of the last ``L`` hours, turned into a z-score against its own previous 30 days (the current
value excluded), so the strategy trades unusual imbalance, not a symbol's constant tilt.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from quanta.research.fetch import symbol_windows
from quanta.research.minute import MIN_NS, Bars, load_bars
from quanta.research.panel import HOUR_NS, Market

Floats = NDArray[np.float64]
Ints = NDArray[np.int64]
QUARTERS = (0, 15, 30, 45)


def opening_imbalance(bars: Bars) -> tuple[Ints, Floats]:
    """(time the value is known, imbalance) for every quarter-hour opening minute."""
    minute_of_hour = (bars.t // MIN_NS) % 60
    sel = np.isin(minute_of_hour, QUARTERS) & (bars.v > 0)
    oi = (2.0 * bars.tb[sel] - bars.v[sel]) / bars.v[sel]
    return bars.t[sel] + MIN_NS, oi


def hourly_mean(avail: Ints, value: Floats, grid: Ints, hours: int) -> tuple[Floats, Ints]:
    """Mean of the values known in (g − hours, g] for each grid time g, and the time the
    newest of them became known (−1 when none)."""
    order = np.argsort(avail, kind="stable")
    a, v = avail[order], value[order]
    cs = np.concatenate([[0.0], np.cumsum(v)])
    hi = np.searchsorted(a, grid, "right")
    lo = np.searchsorted(a, grid - hours * HOUR_NS, "right")
    n = hi - lo
    mean = np.where(n > 0, (cs[hi] - cs[lo]) / np.maximum(n, 1), np.nan)
    last = np.where(n > 0, a[np.maximum(hi - 1, 0)], -1)
    return mean, last


def trailing_z(x: Floats, window: int = 720, min_obs: int = 240) -> Floats:
    """(x_t − mean) / std of the previous ``window`` finite values (x_t itself excluded)."""
    ok = np.isfinite(x)
    v = np.where(ok, x, 0.0)
    c1 = np.concatenate([[0.0], np.cumsum(v)])
    c2 = np.concatenate([[0.0], np.cumsum(v * v)])
    cn = np.concatenate([[0], np.cumsum(ok)])
    t = np.arange(x.size)
    lo = np.maximum(t - window, 0)
    n = cn[t] - cn[lo]
    s1, s2 = c1[t] - c1[lo], c2[t] - c2[lo]
    mean = s1 / np.maximum(n, 1)
    var = s2 / np.maximum(n, 1) - mean * mean
    sd = np.sqrt(np.maximum(var, 0.0))
    return np.where((n >= min_obs) & ok & (sd > 0), (x - mean) / np.where(sd > 0, sd, 1.0), np.nan)


def add_burst_features(
    m: Market,
    root: Path,
    universe: list[tuple[str, int, str]],
    start: str,
    end: str,
    windows: tuple[int, ...],
) -> None:
    """Adds ``qh_imb_{L}`` (z-scored L-hour opening imbalance) for each L to the market."""
    t_len, n = m.shape
    feats = {h: np.full((t_len, n), np.nan) for h in windows}
    avail = {h: np.full((t_len, n), -1, dtype=np.int64) for h in windows}
    for j, sym in enumerate(m.symbols):
        bars = load_bars(root, universe, sym, start, end, symbol_windows(universe, 1, 0).get(sym))
        if bars is None:
            continue
        ta, oi = opening_imbalance(bars)
        for h in windows:
            mean, last = hourly_mean(ta, oi, m.grid, h)
            feats[h][:, j] = trailing_z(mean)
            avail[h][:, j] = last
    for h in windows:
        m.add_feature(f"qh_imb_{h}", feats[h], avail[h])
