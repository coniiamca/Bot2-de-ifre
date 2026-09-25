"""Tier-0 promotion gates (design §17.2), computed from daily returns of every trial of a
hypothesis family. Pure functions; the runner supplies stress runs and breakdowns.

The selected configuration is the full-sample best trial; CPCV and PBO measure how much of
its performance survives out of sample, DSR deflates it by the number of (effective)
trials, and the Newey–West t guards against autocorrelated returns.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

from quanta.research.cv import cpcv, pbo_cscv
from quanta.research.stats import (
    annualize,
    dsr,
    effective_trials,
    moments,
    newey_west_t,
    nw_lags,
    sharpe,
)

Floats = NDArray[np.float64]

T_MIN = 3.4
DSR_MIN = 0.95
PBO_MAX = 0.25
CPCV_POSITIVE_SHARE = 0.8


@dataclass(slots=True)
class Gate:
    name: str
    passed: bool
    value: str
    rule: str


@dataclass(slots=True)
class FamilyStats:
    trials: list[str]
    best: int
    sr_annual: list[float]
    best_sr_annual: float
    best_t_nw: float
    n_eff: float
    mean_corr: float
    dsr: float
    pbo: float
    pbo_slope: float
    cpcv_path_sr_annual: list[float]
    days: int
    n_prior: int = 0  # earlier trials of the family counted in N_eff and DSR
    gates: list[Gate] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(g.passed for g in self.gates)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["passed"] = self.passed
        return d


def family_stats(
    daily: Floats,
    trials: list[str],
    hold_days: int,
    lookback_days: int,
    prior: Floats | None = None,
) -> FamilyStats:
    """``daily``: T × N net daily returns of all pre-registered trials. ``prior``: T × P
    returns of earlier trials of the same family (e.g. the same strategy on another universe,
    decided after seeing their results). The selection, t, PBO and CPCV stay within this
    grid; N_eff and the luck bar of the DSR count every trial of the family."""
    r = np.asarray(daily, dtype=np.float64)
    t, n = r.shape
    srs = [sharpe(r[:, i]) for i in range(n)]
    best = int(np.argmax(srs))
    m = moments(r[:, best])
    if prior is not None and np.shape(prior)[0] != t:
        raise ValueError("prior trials must cover the same days")
    fam = r if prior is None else np.column_stack([r, np.asarray(prior, dtype=np.float64)])
    fam_srs = [sharpe(fam[:, i]) for i in range(fam.shape[1])]
    tc = effective_trials(fam)
    var_sr = float(np.var(fam_srs, ddof=1)) if len(fam_srs) > 1 else 0.0
    d = dsr(srs[best], t, m.skew, m.kurt, tc.n_eff, var_sr)
    t_nw = newey_west_t(r[:, best], nw_lags(t, hold_days))
    pbo = pbo_cscv(r, 16) if n > 1 else None
    cv = cpcv(r, 6, 2, purge=hold_days, embargo=lookback_days + 1)
    path_sr = [annualize(sharpe(p)) for p in cv.paths]
    fs = FamilyStats(
        trials=trials,
        best=best,
        sr_annual=[annualize(s) for s in srs],
        best_sr_annual=annualize(srs[best]),
        best_t_nw=t_nw,
        n_eff=tc.n_eff,
        mean_corr=tc.mean_corr,
        dsr=d,
        pbo=pbo.pbo if pbo else 0.0,
        pbo_slope=pbo.slope if pbo else 0.0,
        cpcv_path_sr_annual=path_sr,
        days=t,
        n_prior=fam.shape[1] - n,
    )
    share = float(np.mean([s > 0 for s in path_sr]))
    fs.gates = [
        Gate(
            "cpcv",
            float(np.median(path_sr)) > 0 and share >= CPCV_POSITIVE_SHARE,
            f"medyan {np.median(path_sr):.2f}, pozitif yol %{share * 100:.0f}",
            f"medyan yol SR > 0 ve yolların ≥ %{CPCV_POSITIVE_SHARE * 100:.0f}'i pozitif",
        ),
        Gate(
            "dsr",
            d >= DSR_MIN,
            f"{d:.3f} (N_eff {tc.n_eff:.1f} / {fam.shape[1]} deneme)",
            f"DSR ≥ {DSR_MIN}",
        ),
        Gate("pbo", fs.pbo <= PBO_MAX, f"{fs.pbo:.2f}", f"PBO ≤ {PBO_MAX}"),
        Gate("t_nw", t_nw >= T_MIN, f"{t_nw:.2f}", f"Newey–West t ≥ {T_MIN}"),
    ]
    return fs


def plateau(sr_annual: list[float], best: int, neighbours: list[int]) -> Gate:
    """Neighbouring parameter sets must keep at least half of the best Sharpe."""
    if not neighbours:
        return Gate("plateau", False, "komşu yok", "komşu ayarlar ≥ en iyinin yarısı")
    nb = float(np.mean([sr_annual[i] for i in neighbours]))
    ok = sr_annual[best] > 0 and nb >= 0.5 * sr_annual[best]
    return Gate(
        "plateau",
        ok,
        f"komşu ort. {nb:.2f} / en iyi {sr_annual[best]:.2f}",
        "komşu ayarların ortalama SR'ı ≥ en iyinin yarısı",
    )


@dataclass(frozen=True, slots=True)
class Alpha:
    alpha_annual: float
    beta: float
    t_nw: float


def alpha_vs(strategy: Floats, benchmark: Floats, hold_days: int) -> Alpha:
    """OLS of strategy on benchmark daily returns; alpha's t from the residual series."""
    s = np.asarray(strategy, dtype=np.float64)
    b = np.asarray(benchmark, dtype=np.float64)
    vb = float(np.var(b, ddof=1))
    beta = float(np.cov(s, b, ddof=1)[0, 1] / vb) if vb > 0 else 0.0
    resid = s - beta * b
    return Alpha(float(resid.mean() * 365.0), beta, newey_west_t(resid, nw_lags(s.size, hold_days)))


def share_positive(values: list[float]) -> float:
    return float(np.mean([v > 0 for v in values])) if values else 0.0


def annual_sr(daily: Floats) -> float:
    return annualize(sharpe(daily))


def max_drawdown(daily: Floats) -> float:
    eq = np.cumprod(1.0 + np.asarray(daily, dtype=np.float64))
    peak = np.maximum.accumulate(eq)
    return float((eq / peak - 1.0).min()) if eq.size else 0.0


def cagr(daily: Floats) -> float:
    d = np.asarray(daily, dtype=np.float64)
    if d.size == 0:
        return 0.0
    total = float(np.prod(1.0 + d))
    return total ** (365.0 / d.size) - 1.0 if total > 0 else -1.0


def safe(x: float) -> float:
    return 0.0 if not math.isfinite(x) else x
