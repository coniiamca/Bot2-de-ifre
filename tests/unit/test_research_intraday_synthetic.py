"""The intraday pipeline on synthetic minute markets with a known answer: a planted
snap-back must pass the gates, a market without it must not, and a signal that peeks one
bar ahead must be caught by its absurd Sharpe."""

from __future__ import annotations

from typing import Any

import pytest

np = pytest.importorskip("numpy")

from quanta.research.gates import annual_sr, family_stats  # noqa: E402
from quanta.research.intraday import (  # noqa: E402
    IntradayCosts,
    Portfolio,
    Signals,
    Trades,
    combine,
    daily_pnl,
    run_trades,
)
from quanta.research.intraday_strategies import prepare, signals  # noqa: E402
from quanta.research.minute import Bars  # noqa: E402
from quanta.research.synthetic import make_minute_bars  # noqa: E402

FIXED: dict[str, Any] = {
    "vol_halflife_min": 1440,
    "warmup_days": 5,
    "vol_ratio_min": 3,
    "tp_frac": 0.5,
    "sl_frac": 1.0,
    "limit_valid": 5,
}
GRID = [
    {"k": k, "z": z, "hold": h, "entry": "market"}
    for k in (5, 15)
    for z in (4, 6)
    for h in (30, 120)
]
DAYS = 300


def _family(markets: list[Bars]) -> tuple[Any, list[np.ndarray]]:
    preps = [prepare(b, FIXED) for b in markets]
    cols = []
    for g in GRID:
        parts = [
            run_trades(b, signals("snapback", p, g, FIXED), IntradayCosts()).with_symbol(j)
            for j, (b, p) in enumerate(zip(markets, preps, strict=True))
        ]
        tr, w = combine(Trades.concat(parts), Portfolio())
        first = int(markets[0].t[0] // 86_400_000_000_000)
        cols.append(daily_pnl(tr, w, first + 5, first + DAYS))
    fs = family_stats(np.column_stack(cols), [str(g) for g in GRID], 1, 1)
    return fs, cols


@pytest.mark.slow
def test_planted_snapback_passes_and_a_permanent_move_does_not() -> None:
    planted = [make_minute_bars(DAYS, seed=s, revert=0.5, symbol=f"S{s}") for s in range(3)]
    fs, _ = _family(planted)
    gates = {g.name: g.passed for g in fs.gates}
    assert gates["dsr"] and gates["cpcv"] and gates["t_nw"], fs.gates
    null = [make_minute_bars(DAYS, seed=10 + s, revert=0.0, symbol=f"N{s}") for s in range(3)]
    fs0, _ = _family(null)
    assert not fs0.passed and fs0.dsr < 0.95 and fs0.best_sr_annual < 1.0


def test_a_signal_that_peeks_one_bar_ahead_is_caught() -> None:
    b = make_minute_bars(60, seed=3)
    n = len(b)
    i = np.arange(1440, n - 3, 30)
    nan = np.full(i.size, np.nan)
    honest_side = np.where(b.c[i] > b.o[i], 1, -1)  # known at the close of bar i
    peek_side = np.where(b.c[i + 1] > b.c[i], 1, -1)  # bar i+1: the future
    first = int(b.t[0] // 86_400_000_000_000)
    zero = IntradayCosts(taker_bps=0, maker_bps=0, slip_bps=(0, 0, 0, 0))
    srs = {}
    for name, side in (("honest", honest_side), ("peek", peek_side)):
        tr = run_trades(b, Signals.build(i, side, nan, nan, nan, 1), zero)
        tr, w = combine(tr, Portfolio(risk_per_trade=1.0, default_risk=1.0))
        srs[name] = annual_sr(daily_pnl(tr, w, first + 1, first + 60))
    assert srs["peek"] > 10 and abs(srs["honest"]) < 3, srs
