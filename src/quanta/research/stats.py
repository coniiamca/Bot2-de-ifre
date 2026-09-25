"""Performance statistics that account for skew, fat tails and the number of trials.

* PSR / DSR — Bailey & López de Prado, "The Sharpe Ratio Efficient Frontier" (2012) and
  "The Deflated Sharpe Ratio" (2014). Sharpe ratios here are per period (daily), not
  annualized; kurtosis is the non-excess (normal = 3) moment.
* Effective number of trials — correlated trials are fewer independent tries:
  ``N_eff = ρ̄ + (1 − ρ̄)·N`` with ρ̄ the mean pairwise correlation (conservative), and the
  eigenvalue participation ratio as a second view.
* Newey–West t-statistic of the mean (overlapping positions make returns autocorrelated).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np
from numpy.typing import NDArray

EULER_GAMMA = 0.5772156649015329
_N = NormalDist()
Floats = NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class Moments:
    n: int
    mean: float
    sd: float
    skew: float
    kurt: float  # non-excess: 3 for a normal distribution


def moments(r: Floats) -> Moments:
    x = np.asarray(r, dtype=np.float64)
    n = int(x.size)
    if n < 2:
        return Moments(n, float(x.mean()) if n else 0.0, 0.0, 0.0, 3.0)
    mean = float(x.mean())
    sd = float(x.std(ddof=1))
    pop = float(x.std(ddof=0))
    if pop == 0.0:
        return Moments(n, mean, sd, 0.0, 3.0)
    z = (x - mean) / pop
    return Moments(n, mean, sd, float((z**3).mean()), float((z**4).mean()))


def sharpe(r: Floats) -> float:
    """Per-period Sharpe ratio (mean / sample sd); 0 for a constant series."""
    m = moments(r)
    return m.mean / m.sd if m.sd > 0 else 0.0


def annualize(sr: float, periods_per_year: float = 365.0) -> float:
    return sr * math.sqrt(periods_per_year)


def psr(sr: float, sr_star: float, t: int, skew: float, kurt: float) -> float:
    """Probability that the true Sharpe exceeds ``sr_star`` given ``t`` observations."""
    if t < 2:
        return 0.5
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    denom = max(denom, 1e-12)
    return _N.cdf((sr - sr_star) * math.sqrt(t - 1) / math.sqrt(denom))


def expected_max_sr(n_eff: float, var_sr: float) -> float:
    """Expected maximum Sharpe of ``n_eff`` independent zero-skill trials whose Sharpe
    ratios have variance ``var_sr`` (the DSR benchmark SR₀)."""
    if n_eff <= 1.0 or var_sr <= 0.0:
        return 0.0
    a = _N.inv_cdf(1.0 - 1.0 / n_eff)
    b = _N.inv_cdf(1.0 - 1.0 / (n_eff * math.e))
    return math.sqrt(var_sr) * ((1.0 - EULER_GAMMA) * a + EULER_GAMMA * b)


def dsr(sr: float, t: int, skew: float, kurt: float, n_eff: float, var_sr: float) -> float:
    """Deflated Sharpe ratio: PSR against the best Sharpe expected from luck alone."""
    return psr(sr, expected_max_sr(n_eff, var_sr), t, skew, kurt)


@dataclass(frozen=True, slots=True)
class TrialCount:
    n: int
    mean_corr: float
    n_eff: float  # ρ̄ + (1 − ρ̄)·N  (used by the gates)
    participation: float  # (Σλ)² / Σλ² of the correlation matrix


def effective_trials(returns: Floats) -> TrialCount:
    """``returns``: T × N matrix of per-period trial returns."""
    r = np.asarray(returns, dtype=np.float64)
    n = int(r.shape[1]) if r.ndim == 2 else 1
    if n < 2:
        return TrialCount(n, 1.0, float(n), float(n))
    live = r[:, r.std(axis=0) > 0]
    if live.shape[1] < 2:
        return TrialCount(n, 1.0, 1.0, 1.0)
    c = np.corrcoef(live, rowvar=False)
    m = int(live.shape[1])
    rho = float((c.sum() - np.trace(c)) / (m * (m - 1)))
    rho = min(1.0, max(0.0, rho))
    eig = np.clip(np.linalg.eigvalsh(c), 0.0, None)
    participation = float(eig.sum() ** 2 / (eig**2).sum()) if (eig**2).sum() > 0 else 1.0
    return TrialCount(n, rho, rho + (1.0 - rho) * n, participation)


def nw_lags(t: int, hold_periods: int = 1) -> int:
    return max(int(hold_periods), math.ceil(4.0 * float((t / 100.0) ** (2.0 / 9.0))))


def newey_west_t(r: Floats, lags: int) -> float:
    """t-statistic of the mean with a Bartlett-kernel HAC variance."""
    x = np.asarray(r, dtype=np.float64)
    t = x.size
    if t < 3:
        return 0.0
    d = x - x.mean()
    var = float(d @ d) / t
    for lag in range(1, min(lags, t - 1) + 1):
        w = 1.0 - lag / (lags + 1.0)
        var += 2.0 * w * float(d[lag:] @ d[:-lag]) / t
    if var <= 0:
        return 0.0
    return float(x.mean()) / math.sqrt(var / t)
