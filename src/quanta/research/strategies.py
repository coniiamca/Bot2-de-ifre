"""Strategy rules for the pre-registered hypotheses. Pure functions: point-in-time features
in, target weights (T × N, fraction of equity) out. They read ``market.features`` only —
never execution-side prices of the current bar.

H6 — time-series momentum (trend following) on hourly bars, volatility-targeted with equal
risk per asset; the benchmark is the same machinery always long. H5 — the quarter-hour opening
imbalance (research.burst) through the same weighting machinery.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from quanta.research.panel import Market

Floats = NDArray[np.float64]
HOURS_PER_YEAR = 24 * 365


@dataclass(frozen=True, slots=True)
class TrendParams:
    lookback_h: int
    rebalance_h: int
    signal: str  # "sign" | "scaled"
    vol_halflife_h: int = 168
    min_history_h: int = 336
    target_vol_annual: float = 0.20
    cap_asset: float = 0.5
    cap_gross: float = 1.5
    band: float = 0.02  # no trade unless the target moves by more than this (of equity)
    long_only: bool = False  # benchmark


def ewma_vol(logret: Floats, halflife_h: int) -> Floats:
    """EWMA of squared hourly log returns, point-in-time (uses returns up to t)."""
    a = 1.0 - 0.5 ** (1.0 / halflife_h)
    out = np.full_like(logret, np.nan)
    var = np.full(logret.shape[1], np.nan)
    for t in range(logret.shape[0]):
        x = logret[t]
        ok = np.isfinite(x)
        fresh = ok & ~np.isfinite(var)
        var = np.where(fresh, x * x, var)
        upd = ok & ~fresh
        var = np.where(upd, (1.0 - a) * var + a * x * x, var)
        out[t] = np.sqrt(var)
    return out


def trend_weights(m: Market, p: TrendParams) -> Floats:
    close = m.features["close"]  # close of the last finished bar, known at grid[t]
    n = m.shape[1]
    logc = np.log(close)
    logret = np.vstack([np.full((1, n), np.nan), np.diff(logc, axis=0)])
    vol = ewma_vol(logret, p.vol_halflife_h)
    lag = np.full_like(logc, np.nan)
    lag[p.lookback_h :] = logc[: -p.lookback_h]
    mom = logc - lag
    if p.long_only:
        sig = np.where(np.isfinite(mom), 1.0, np.nan)
    elif p.signal == "sign":
        sig = np.sign(mom)
    else:
        sig = np.clip(mom / (vol * math.sqrt(p.lookback_h)), -2.0, 2.0) / 2.0
    return signal_weights(m, sig, vol, p)


def signal_weights(m: Market, sig: Floats, vol: Floats, p: TrendParams) -> Floats:
    """Signal in [−1, 1] per cell → volatility-targeted weights with equal risk per asset,
    per-asset and gross caps, the rebalance schedule and the no-trade band."""
    close = m.features["close"]
    t_len, n = m.shape
    # history: at least min_history_h consecutive finite closes
    finite = np.isfinite(close).astype(np.int64)
    run = np.zeros_like(finite)
    for t in range(t_len):
        run[t] = (run[t - 1] + 1) * finite[t] if t else finite[t]
    eligible = m.universe & (run >= p.min_history_h) & np.isfinite(sig) & (vol > 0)
    active = eligible.sum(axis=1, keepdims=True)
    target_h = p.target_vol_annual / math.sqrt(HOURS_PER_YEAR)
    raw = np.where(
        eligible, sig * target_h / np.where(vol > 0, vol, np.inf) / np.maximum(active, 1), 0.0
    )
    raw = np.clip(raw, -p.cap_asset, p.cap_asset)
    gross = np.abs(raw).sum(axis=1, keepdims=True)
    raw = raw * np.minimum(1.0, p.cap_gross / np.where(gross > 0, gross, 1.0))
    # rebalance schedule + no-trade band; leaving the universe always exits
    out = np.zeros_like(raw)
    cur = np.zeros(n)
    hour = (m.grid // (3600 * 10**9)).astype(np.int64)
    for t in range(t_len):
        if hour[t] % p.rebalance_h == 0:
            move = np.abs(raw[t] - cur) > p.band
            cur = np.where(move, raw[t], cur)
        cur = np.where(eligible[t], cur, 0.0)
        out[t] = cur
    return out


def hourly_vol(m: Market, p: TrendParams) -> Floats:
    close = m.features["close"]
    logc = np.log(close)
    logret = np.vstack([np.full((1, m.shape[1]), np.nan), np.diff(logc, axis=0)])
    return ewma_vol(logret, p.vol_halflife_h)


def burst_weights(m: Market, p: TrendParams) -> Floats:
    """H5: sign / scaled z-score of the quarter-hour opening imbalance averaged over the last
    ``lookback_h`` hours (feature ``qh_imb_{lookback_h}``)."""
    z = m.features[f"qh_imb_{p.lookback_h}"]
    sig = np.sign(z) if p.signal == "sign" else np.clip(z, -2.0, 2.0) / 2.0
    return signal_weights(m, sig, hourly_vol(m, p), p)
