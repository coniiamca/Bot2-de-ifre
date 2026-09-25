"""Bar-level portfolio backtest (Tier-0): target weights in, net returns out.

Every hour ``t``, in this order:

1. **Funding** is charged on the weights held into ``grid[t]`` (a funding timestamp charges
   the position open at that moment): ``−Σ w·rate`` — longs pay positive rates.
2. **Trade** at ``px[t]`` to the target (``target[t − exec_lag]``); an asset that cannot be
   traded keeps its weight. Cost = Σ |Δw|·bps. An asset whose next price is missing
   (delisting, data gap) is closed now at ``forced_exit_mult`` × cost.
3. **Return** over [t, t+1): ``R = Σ w·r − cost − funding``, ``r = px[t+1]/px[t] − 1``.
4. **Drift**: ``w ← w·(1 + r)/(1 + R)``.

Weights are fractions of equity (negative = short). The last hour has no return. Intrabar
extremes flag possible liquidations: with the plan's isolated margin and ≤3× leverage per
symbol, an adverse move of ≥30 % within a bar would liquidate a position.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from quanta.research.costs import CostModel
from quanta.research.panel import Market, day_index

Floats = NDArray[np.float64]
LIQUIDATION_MOVE = 0.30


@dataclass(slots=True)
class Result:
    ret: Floats  # net hourly return (T,)
    gross: Floats
    cost: Floats
    funding: Floats
    turnover: Floats  # Σ|Δw| per hour
    weights: Floats  # held weights after trading (T × N)
    liquidation_flags: int

    def daily(self, grid: NDArray[np.int64]) -> tuple[NDArray[np.int64], Floats]:
        """Compounded net returns per UTC day."""
        return daily_returns(grid, self.ret)


def daily_returns(grid: NDArray[np.int64], hourly: Floats) -> tuple[NDArray[np.int64], Floats]:
    days = day_index(grid)
    uniq, start = np.unique(days, return_index=True)
    logs = np.log1p(np.maximum(hourly, -0.999999))
    sums = np.add.reduceat(logs, start) if logs.size else np.zeros(0)
    return uniq, np.expm1(sums)


def simulate(
    target: Floats,
    market: Market,
    costs: CostModel,
    exec_lag: int = 0,
    price: str = "open",
) -> Result:
    px = market.open if price == "open" else market.vwap
    t_len, n = market.shape
    bps = costs.bps(market.rank) / 1e4
    w = np.zeros(n)
    out_ret = np.zeros(t_len)
    out_gross = np.zeros(t_len)
    out_cost = np.zeros(t_len)
    out_fund = np.zeros(t_len)
    out_turn = np.zeros(t_len)
    weights = np.zeros((t_len, n))
    liq = 0
    tgt_all = np.nan_to_num(target, nan=0.0)
    for t in range(t_len - 1):
        f = -float(np.dot(w, market.funding[t]))
        tgt = tgt_all[t - exec_lag] if t >= exec_lag else np.zeros(n)
        can = market.tradable[t] & np.isfinite(px[t])
        new = np.where(can, tgt, w)
        nxt_ok = np.isfinite(px[t + 1]) & np.isfinite(px[t])
        forced = ~nxt_ok & (new != 0)
        new = np.where(nxt_ok, new, 0.0)
        dw = np.abs(new - w)
        mult = np.where(forced, costs.forced_exit_mult, 1.0)
        cost = float(np.sum(dw * bps[t] * mult))
        w = new
        r = np.where(nxt_ok, px[t + 1] / np.where(nxt_ok, px[t], 1.0) - 1.0, 0.0)
        gross = float(np.dot(w, r))
        total = gross - cost + f
        # intrabar liquidation check against the entry price of the bar
        up = np.where(w < 0, market.high[t] / px[t] - 1.0, 0.0)
        down = np.where(w > 0, 1.0 - market.low[t] / px[t], 0.0)
        liq += int(np.any(np.nan_to_num(np.maximum(up, down)) >= LIQUIDATION_MOVE))
        out_ret[t], out_gross[t], out_cost[t], out_fund[t] = total, gross, cost, f
        out_turn[t] = float(dw.sum())
        weights[t] = w
        w = w * (1.0 + r) / (1.0 + total) if total > -1.0 else np.zeros(n)
    return Result(out_ret, out_gross, out_cost, out_fund, out_turn, weights, liq)
