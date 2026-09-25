"""Out-of-sample validation for rule-based strategies whose only "fit" is the choice of
parameters from a pre-registered grid.

* **CPCV** — combinatorial purged cross-validation (López de Prado 2018, ch. 12): the time
  axis is cut into ``n_groups`` blocks; every choice of ``k`` test blocks is one split. On a
  split the parameter set with the best training Sharpe is selected and its returns on the
  test blocks are kept; the splits combine into ``k/n·C(n,k)`` full out-of-sample paths.
  Training observations within ``purge`` periods before and ``embargo`` periods after a test
  block are dropped (positions and look-back windows straddle the boundary).
  Every trial is simulated once over the whole period, then sliced: no warm-up restarts.
* **PBO** — probability of backtest overfitting via CSCV (Bailey, Borwein, López de Prado,
  Zhu 2017): how often the in-sample best configuration ranks in the bottom half
  out of sample.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

Floats = NDArray[np.float64]
Ints = NDArray[np.int64]


def group_bounds(n_obs: int, n_groups: int) -> list[tuple[int, int]]:
    edges = np.linspace(0, n_obs, n_groups + 1).round().astype(int)
    return [(int(edges[i]), int(edges[i + 1])) for i in range(n_groups)]


@dataclass(frozen=True, slots=True)
class Split:
    test_groups: tuple[int, ...]
    train: Ints
    test: Ints


def cpcv_splits(
    n_obs: int, n_groups: int = 6, k: int = 2, purge: int = 0, embargo: int = 0
) -> list[Split]:
    bounds = group_bounds(n_obs, n_groups)
    out: list[Split] = []
    for combo in itertools.combinations(range(n_groups), k):
        test_mask = np.zeros(n_obs, dtype=bool)
        drop = np.zeros(n_obs, dtype=bool)
        for g in combo:
            lo, hi = bounds[g]
            test_mask[lo:hi] = True
            drop[max(0, lo - purge) : lo] = True
            drop[hi : min(n_obs, hi + embargo)] = True
        train = np.flatnonzero(~test_mask & ~drop)
        out.append(Split(combo, train, np.flatnonzero(test_mask)))
    return out


def n_paths(n_groups: int, k: int) -> int:
    return k * math.comb(n_groups, k) // n_groups


def _sharpe_cols(r: Floats) -> Floats:
    if r.shape[0] < 2:  # purge/embargo left (almost) no training data: no preference
        return np.zeros(r.shape[1])
    sd = r.std(axis=0, ddof=1)
    mean = r.mean(axis=0)
    return np.divide(mean, sd, out=np.zeros_like(mean), where=sd > 0)


@dataclass(frozen=True, slots=True)
class CPCVResult:
    paths: Floats  # n_paths × T out-of-sample returns
    selected: list[int]  # chosen trial per split
    splits: list[Split]


def cpcv(
    returns: Floats, n_groups: int = 6, k: int = 2, purge: int = 0, embargo: int = 0
) -> CPCVResult:
    """``returns``: T × N per-period returns of N parameter sets."""
    r = np.asarray(returns, dtype=np.float64)
    t = r.shape[0]
    splits = cpcv_splits(t, n_groups, k, purge, embargo)
    selected = [int(np.argmax(_sharpe_cols(r[s.train]))) for s in splits]
    bounds = group_bounds(t, n_groups)
    # path p takes, for every group, the p-th split (in order) that tests that group
    testers: dict[int, list[int]] = {g: [] for g in range(n_groups)}
    for i, s in enumerate(splits):
        for g in s.test_groups:
            testers[g].append(i)
    paths = np.zeros((n_paths(n_groups, k), t))
    for p in range(paths.shape[0]):
        for g, (lo, hi) in enumerate(bounds):
            paths[p, lo:hi] = r[lo:hi, selected[testers[g][p]]]
    return CPCVResult(paths, selected, splits)


@dataclass(frozen=True, slots=True)
class PBOResult:
    pbo: float
    logits: Floats
    is_sr: Floats  # in-sample Sharpe of the selected trial per split
    oos_sr: Floats  # its out-of-sample Sharpe
    slope: float  # OOS-on-IS regression slope (degradation; < 1 is typical)
    p_oos_loss: float  # share of splits where the selected trial loses out of sample


def pbo_cscv(returns: Floats, n_blocks: int = 16) -> PBOResult:
    r = np.asarray(returns, dtype=np.float64)
    t, n = r.shape
    bounds = group_bounds(t, n_blocks)
    s1 = np.array([r[lo:hi].sum(axis=0) for lo, hi in bounds])  # blocks × N
    s2 = np.array([(r[lo:hi] ** 2).sum(axis=0) for lo, hi in bounds])
    cnt = np.array([hi - lo for lo, hi in bounds], dtype=np.float64)
    combos = np.array(
        [
            [b in c for b in range(n_blocks)]
            for c in itertools.combinations(range(n_blocks), n_blocks // 2)
        ],
        dtype=np.float64,
    )

    def sr(mask: Floats) -> Floats:
        s = mask @ s1
        ss = mask @ s2
        c = (mask @ cnt)[:, None]
        var = (ss - s * s / c) / np.maximum(c - 1.0, 1.0)
        sd = np.sqrt(np.maximum(var, 0.0))
        out: Floats = np.divide(s / c, sd, out=np.zeros_like(s), where=sd > 0)
        return out

    is_all = sr(combos)
    oos_all = sr(1.0 - combos)
    best = np.argmax(is_all, axis=1)
    rows = np.arange(len(combos))
    v = oos_all[rows, best]
    less = (oos_all < v[:, None]).sum(axis=1)
    equal = (oos_all == v[:, None]).sum(axis=1)
    rank = less + (equal + 1) / 2.0  # average rank, 1 = worst
    omega = rank / (n + 1.0)
    logits = np.log(omega / (1.0 - omega))
    is_best = is_all[rows, best]
    slope = float(np.polyfit(is_best, v, 1)[0]) if np.ptp(is_best) > 0 else 0.0
    return PBOResult(float((logits <= 0).mean()), logits, is_best, v, slope, float((v < 0).mean()))
