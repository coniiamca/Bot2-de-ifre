"""End-to-end self-test of the research pipeline on synthetic markets with known truth:
a planted trend (true Sharpe ≈ 2) must pass the family gates, pure noise must not."""

from __future__ import annotations

from typing import Any

import numpy as np

from quanta.research.backtest import simulate
from quanta.research.costs import CostModel
from quanta.research.gates import family_stats
from quanta.research.strategies import TrendParams, trend_weights
from quanta.research.synthetic import make_market

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


def selftest() -> dict[str, Any]:
    planted = _family(0.035, seed=11)
    noise = _family(0.0, seed=12)
    ok = planted["gates"]["dsr"] and planted["gates"]["cpcv"] and not noise["passed"]
    return {"planted_trend": planted, "noise": noise, "ok": ok}
