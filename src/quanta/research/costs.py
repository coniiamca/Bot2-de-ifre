"""Trading cost model for the bar-level backtest (Tier-0).

Cost per unit of traded notional = taker fee + half-spread/slippage, by liquidity tier. The
defaults are deliberately pessimistic for a small account (VIP0, no BNB discount, market
orders); they are replaced by values measured from our own recorded order books before any
real-money step. Stress runs multiply the whole cost by 1.5 and 2.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

Floats = NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class CostModel:
    fee_bps: float = 5.0  # Binance USDⓈ-M VIP0 taker
    slip_top2_bps: float = 1.0  # universe rank 1–2 (BTC, ETH)
    slip_rest_bps: float = 3.0  # rank 3–10 and anything leaving the universe
    forced_exit_mult: float = 3.0  # delisting / data gap: exit at a bad price
    stress: float = 1.0

    def bps(self, rank: Floats) -> Floats:
        """Cost in basis points per unit turnover for each cell of a rank matrix."""
        slip = np.where(np.isfinite(rank) & (rank <= 2), self.slip_top2_bps, self.slip_rest_bps)
        return (self.fee_bps + slip) * self.stress

    def scaled(self, stress: float) -> CostModel:
        return CostModel(
            self.fee_bps, self.slip_top2_bps, self.slip_rest_bps, self.forced_exit_mult, stress
        )
