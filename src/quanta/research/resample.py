"""Stationary bootstrap (Politis & Romano 1994) with automatic block length
(Politis & White 2004, corrected by Patton, Politis & White 2009).

Returns are autocorrelated (overlapping positions, volatility clustering), and in a crash all
assets move together: resampling keeps blocks of consecutive periods and, for panels, the
same time indices for every asset.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
from numpy.typing import NDArray

Floats = NDArray[np.float64]
Ints = NDArray[np.int64]


def stationary_bootstrap_idx(n: int, b: float, n_boot: int, rng: np.random.Generator) -> Ints:
    """``n_boot × n`` resampled time indices; blocks have geometric lengths of mean ``b``
    and wrap around the end (circular)."""
    if n <= 0:
        return np.zeros((n_boot, 0), dtype=np.int64)
    p = 1.0 / max(1.0, b)
    idx = np.empty((n_boot, n), dtype=np.int64)
    idx[:, 0] = rng.integers(0, n, size=n_boot)
    new_block = rng.random((n_boot, n)) < p
    starts = rng.integers(0, n, size=(n_boot, n))
    for i in range(1, n):
        nxt = (idx[:, i - 1] + 1) % n
        idx[:, i] = np.where(new_block[:, i], starts[:, i], nxt)
    return idx


def _flat_top(t: Floats) -> Floats:
    a = np.abs(t)
    return np.where(a <= 0.5, 1.0, np.where(a <= 1.0, 2.0 * (1.0 - a), 0.0))


def block_length(x: Floats) -> float:
    """Optimal mean block length for the stationary bootstrap of ``x``."""
    v = np.asarray(x, dtype=np.float64)
    n = v.size
    if n < 8:
        return 1.0
    d = v - v.mean()
    var0 = float(d @ d) / n
    if var0 <= 0:
        return 1.0
    kn = max(5, math.ceil(math.sqrt(math.log10(n))))
    m_max = math.ceil(math.sqrt(n)) + kn
    m_max = min(m_max, n - 1)
    acov = np.array([float(d[k:] @ d[: n - k]) / n for k in range(m_max + 1)])
    rho = acov / var0
    thr = 2.0 * math.sqrt(math.log10(n) / n)
    m_hat = m_max
    for m in range(1, m_max - kn + 1):
        if np.all(np.abs(rho[m + 1 : m + 1 + kn]) < thr):
            m_hat = m
            break
    big_m = min(2 * m_hat, m_max)
    ks = np.arange(-big_m, big_m + 1)
    lam = _flat_top(ks / big_m) if big_m > 0 else np.ones(1)
    r = acov[np.abs(ks)]
    g_hat = float((lam * np.abs(ks) * r).sum())
    g0 = float((lam * r).sum())
    d_sb = 2.0 * g0 * g0
    if d_sb <= 0 or g_hat == 0:
        return 1.0
    b = (2.0 * g_hat * g_hat / d_sb) ** (1.0 / 3.0) * n ** (1.0 / 3.0)
    return float(min(max(1.0, b), 3.0 * math.sqrt(n), n / 3.0))


def bootstrap(
    data: Floats,
    stat: Callable[[Floats], float],
    b: float,
    n_boot: int = 2000,
    seed: int = 0,
) -> Floats:
    """Bootstrap distribution of ``stat`` over rows (time) of ``data`` (1-D or T × N)."""
    arr = np.asarray(data, dtype=np.float64)
    rng = np.random.default_rng(seed)
    idx = stationary_bootstrap_idx(arr.shape[0], b, n_boot, rng)
    return np.array([stat(arr[row]) for row in idx], dtype=np.float64)


def percentile_ci(dist: Floats, alpha: float = 0.05) -> tuple[float, float]:
    lo, hi = np.quantile(dist, [alpha / 2.0, 1.0 - alpha / 2.0])
    return float(lo), float(hi)
