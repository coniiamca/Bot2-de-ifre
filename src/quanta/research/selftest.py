"""End-to-end self-test of the research pipeline on synthetic markets with known truth:
a planted trend (true Sharpe ≈ 2) must pass the family gates, pure noise must not; on
1-minute bars, a planted snap-back after sharp moves must pass and a permanent move not."""

from __future__ import annotations

from typing import Any

import numpy as np

from quanta.research.backtest import simulate
from quanta.research.costs import CostModel
from quanta.research.gates import family_stats
from quanta.research.intraday import (
    IntradayCosts,
    Portfolio,
    Trades,
    combine,
    daily_pnl,
    run_trades,
)
from quanta.research.intraday_strategies import prepare, signals
from quanta.research.strategies import TrendParams, trend_weights
from quanta.research.synthetic import make_market, make_minute_bars

GRID = [TrendParams(lb, 24, sig) for lb in (72, 168) for sig in ("sign", "scaled")]


def _family(beta: float, seed: int) -> dict[str, Any]:
    m = make_market(6, 24 * 365 * 3, seed=seed, tsmom_beta=beta)
    m.check_point_in_time()
    cols = [simulate(trend_weights(m, p), m, CostModel()).daily(m.grid)[1] for p in GRID]
    fs = family_stats(np.column_stack(cols), [str(p) for p in GRID], hold_days=7, lookback_days=7)
    return {
        "best_sr_annual": round(fs.best_sr_annual, 2),
        "dsr": round(fs.dsr, 3),
        "pbo": round(fs.pbo, 2),
        "gates": {g.name: g.passed for g in fs.gates},
        "passed": fs.passed,
    }


SNAP_FIXED: dict[str, Any] = {
    "vol_halflife_min": 1440,
    "warmup_days": 5,
    "vol_ratio_min": 3,
    "tp_frac": 0.5,
    "sl_frac": 1.0,
    "limit_valid": 5,
}
SNAP_GRID = [{"k": k, "z": z, "hold": 30, "entry": "market"} for k in (5, 15) for z in (4, 6)]


def _snapback(revert: float, seed: int, days: int = 300) -> dict[str, Any]:
    bars = [make_minute_bars(days, seed=seed + j, revert=revert, symbol=f"S{j}") for j in range(3)]
    preps = [prepare(b, SNAP_FIXED) for b in bars]
    first = int(bars[0].t[0] // 86_400_000_000_000) + 5
    cols = []
    for g in SNAP_GRID:
        parts = [
            run_trades(b, signals("snapback", p, g, SNAP_FIXED), IntradayCosts()).with_symbol(j)
            for j, (b, p) in enumerate(zip(bars, preps, strict=True))
        ]
        tr, w = combine(Trades.concat(parts), Portfolio())
        cols.append(daily_pnl(tr, w, first, first + days - 5))
    fs = family_stats(np.column_stack(cols), [str(g) for g in SNAP_GRID], 1, 1)
    return {
        "best_sr_annual": round(fs.best_sr_annual, 2),
        "dsr": round(fs.dsr, 3),
        "gates": {g.name: g.passed for g in fs.gates},
        "passed": fs.passed,
    }


def selftest() -> dict[str, Any]:
    planted = _family(0.035, seed=11)
    noise = _family(0.0, seed=12)
    snap = _snapback(0.5, seed=21)
    perm = _snapback(0.0, seed=31)
    ok = (
        planted["gates"]["dsr"]
        and planted["gates"]["cpcv"]
        and not noise["passed"]
        and snap["gates"]["dsr"]
        and snap["gates"]["cpcv"]
        and not perm["passed"]
    )
    return {
        "planted_trend": planted,
        "noise": noise,
        "planted_snapback": snap,
        "permanent_moves": perm,
        "ok": ok,
    }
