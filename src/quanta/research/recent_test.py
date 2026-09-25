"""A one-shot test of "video-style" settings of model M1 on the most recent months only
(``research/prereg/V1.yaml``): fixed capital in dollars, a fixed position size per trade, many
trades a day, results as dollars per day.

The model and its monthly walk-forward are M1's (:mod:`quanta.research.ml_runner`). Trades run
on the real USDC-margined contracts (maker 0, taker 4 bps) with the minute engine. Each
configuration is judged on its own against outcome classes written down before the run; no
configuration is selected.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from quanta.core.log import get_logger
from quanta.research.gates import max_drawdown
from quanta.research.intraday import Portfolio, Trades, combine, daily_pnl
from quanta.research.intraday_runner import _stats, costs_of
from quanta.research.ledger import Ledger, trial_id
from quanta.research.minute import MIN_NS, month_of
from quanta.research.ml import entries, walk_forward
from quanta.research.ml_runner import (
    Order,
    Settings,
    build_frame,
    data_sha,
    simulate,
    split_orders,
    usdc_eligibility,
)
from quanta.research.prereg import Prereg, git_state
from quanta.research.runner import _day, _iso
from quanta.research.stats import newey_west_t, nw_lags
from quanta.research.universe import month_range, read_universe

log = get_logger(__name__)
Floats = NDArray[np.float64]

OUTCOMES = {
    "HEDEF": "Hedefe ulaştı",
    "KARLI": "Kârlı ama hedefin altında",
    "KANITSIZ": "Kâr kanıtlanamadı",
}


@dataclass(frozen=True, slots=True)
class Sizing:
    capital_usd: float
    notional_mult: float  # each trade's size as a multiple of the capital
    max_positions: int

    def portfolio(self) -> Portfolio:
        # a huge risk budget makes the per-symbol cap bind: every trade gets notional_mult
        return Portfolio(
            risk_per_trade=1e9,
            cap_symbol=self.notional_mult,
            cap_gross=self.notional_mult * self.max_positions,
            max_positions=self.max_positions,
        )


def dollar_stats(
    tr: Trades,
    w: Floats,
    daily: Floats,
    first_day: int,
    capital: float,
    limits: list[float],
) -> dict[str, Any]:
    """Results in dollars on a fixed capital (no compounding)."""
    usd = daily * capital
    n_days = max(usd.size, 1)
    eq = capital + np.cumsum(usd)
    peak = np.maximum.accumulate(np.concatenate([[capital], eq]))[1:]
    months = np.array([_iso(first_day + d)[:7] for d in range(usd.size)])
    exit_day = np.clip(tr.exit_t // (86400 * 10**9) - first_day, 0, max(usd.size - 1, 0))
    trade_month = months[exit_day] if len(tr) and usd.size else np.zeros(0, dtype=months.dtype)
    monthly = {
        m: {
            "usd": float(usd[months == m].sum()),
            "days": int((months == m).sum()),
            "trades": int((trade_month == m).sum()),
        }
        for m in sorted(set(months))
    }
    per_trade = w * tr.net * capital if len(tr) else np.zeros(0)
    return {
        "days": int(usd.size),
        "avg_day_usd": float(usd.mean()) if usd.size else 0.0,
        "median_day_usd": float(np.median(usd)) if usd.size else 0.0,
        "best_day_usd": float(usd.max()) if usd.size else 0.0,
        "worst_day_usd": float(usd.min()) if usd.size else 0.0,
        "positive_days": float(np.mean(usd > 0)) if usd.size else 0.0,
        "total_usd": float(usd.sum()),
        "max_drawdown_usd": float((eq - peak).min()) if usd.size else 0.0,
        "max_drawdown_pct": max_drawdown(daily) if usd.size else 0.0,
        "t_nw": newey_west_t(usd, nw_lags(usd.size, 1)) if usd.size > 2 else 0.0,
        "trades_per_day": len(tr) / n_days,
        "avg_trade_usd": float(per_trade.mean()) if len(tr) else 0.0,
        "fees_usd": float(np.sum(w * tr.fee) * capital) if len(tr) else 0.0,
        "slippage_usd": float(np.sum(w * tr.slip) * capital) if len(tr) else 0.0,
        "funding_usd": float(np.sum(w * tr.funding) * capital) if len(tr) else 0.0,
        "loss_days": {f"{int(x)}": int(np.sum(usd <= -x)) for x in limits},
        "monthly": monthly,
    }


def outcome(s: dict[str, Any], fixed: dict[str, Any]) -> str:
    target = float(fixed.get("target_usd_day", 200.0))
    max_dd = float(fixed.get("max_drawdown_usd", 2500.0))
    min_trades = float(fixed.get("min_trades_day", 20.0))
    if (
        s["avg_day_usd"] >= target
        and -s["max_drawdown_usd"] <= max_dd
        and s["trades_per_day"] >= min_trades
    ):
        return "HEDEF"
    if s["avg_day_usd"] > 0 and s["t_nw"] >= 2.0:
        return "KARLI"
    return "KANITSIZ"


def run_recent(
    prereg: Prereg,
    prereg_sha: str,
    root: Path,
    repo: Path,
    *,
    final: bool = False,
    exploratory: bool = False,
    workers: int | None = None,
) -> dict[str, Any]:
    """``dev_end`` is the first test day and ``lockbox_end`` the end (exclusive): the test
    period is the most recent, previously unseen data. ``final`` has no meaning here."""
    fixed = prereg.fixed
    st = Settings.of(fixed)
    sizing = Sizing(
        float(fixed.get("capital_usd", 5000.0)),
        float(fixed.get("notional_mult", 1.0)),
        int(fixed.get("max_positions", 6)),
    )
    configs: dict[str, dict[str, Any]] = fixed["configs"]
    names = [str(c) for c in prereg.grid["config"]]
    universe = read_universe(repo / prereg.universe_file)
    costs = costs_of(prereg)
    first_day, end_day = _day(prereg.dev_end), _day(prereg.lockbox_end)
    test_months = month_range(prereg.dev_end[:7], _iso(end_day - 1)[:7])
    horizons = tuple(sorted({int(configs[n]["horizon"]) for n in names}))
    frame, symbols = build_frame(
        root, universe, st.top, prereg.data_start, prereg.lockbox_end, horizons, workers
    )
    months = month_of(frame.t - MIN_NS)
    eligible = usdc_eligibility(root, symbols, st, test_months[-1], workers)
    sym_names = np.array(symbols)[frame.sym]
    usdc_ok = np.array(
        [eligible.get((str(m), str(s)), False) for m, s in zip(months, sym_names, strict=True)]
    )
    in_universe = (frame.rank >= 1) & (frame.rank <= st.top)
    tradable = in_universe & usdc_ok
    orders: dict[str, dict[int, Order]] = {}
    importance: dict[str, dict[str, float]] = {}
    for name in names:
        h, q = int(configs[name]["horizon"]), float(configs[name]["q"])
        fc = walk_forward(frame, h, st.model("lgbm", h), test_months, in_universe, (q,))
        rows, side = entries(frame, fc, q, tradable, months)
        orders[name] = split_orders(frame, rows, side, h)
        importance[name] = dict(sorted(fc.importance.items(), key=lambda kv: -kv[1])[:8])
    trades = simulate(
        root,
        universe,
        symbols,
        prereg.dev_end,
        prereg.lockbox_end,
        orders,
        {"base": (costs, 0, "maker")},
        st,
        usdc=True,
        workers=workers,
    )
    pf = sizing.portfolio()
    limits = [float(x) for x in fixed.get("loss_day_limits_usd", [150, 1000])]
    commit, dirty = git_state(repo)
    exploratory = exploratory or dirty
    dsha = data_sha(repo, prereg.data_manifests or None)
    period = f"{prereg.dev_end}..{prereg.lockbox_end}"
    ledger = Ledger(repo)
    base = {
        "hypothesis": prereg.id,
        "version": prereg.version,
        "prereg_sha": prereg_sha,
        "commit": commit,
        "exploratory": exploratory,
        "data_sha": dsha,
        "period": period,
    }
    day_axis = np.arange(first_day, end_day, dtype=np.int64)
    results: dict[str, Any] = {}
    for name in names:
        tr, w = combine(trades.get((name, "base"), Trades.concat([])), pf)
        daily = daily_pnl(tr, w, first_day, end_day)
        s = dollar_stats(tr, w, daily, first_day, sizing.capital_usd, limits)
        s["engine"] = _stats(tr, w, end_day - first_day)
        s["signals"] = int(sum(o[0].size for o in orders[name].values()))
        s["outcome"] = outcome(s, fixed)
        s["params"] = configs[name]
        s["features"] = importance[name]
        coins: dict[str, float] = {}
        if len(tr):
            by = np.bincount(tr.symbol, weights=w * tr.net, minlength=len(symbols))
            coins = {symbols[i]: float(by[i] * sizing.capital_usd) for i in np.unique(tr.symbol)}
        s["coins_usd"] = dict(sorted(coins.items(), key=lambda kv: -kv[1]))
        results[name] = s
        ledger.record_trial(
            base,
            trial_id(prereg.id, prereg.version, {"config": name}, dsha, period),
            {"config": name, **configs[name]},
            day_axis,
            daily,
            {
                "avg_day_usd": round(s["avg_day_usd"], 2),
                "days": int(end_day - first_day),
                "trades": len(tr),
            },
        )
        log.info("recent_config_done", config=name, trades=len(tr), outcome=s["outcome"])
    return {
        "hypothesis": prereg.id,
        "title": prereg.title,
        "strategy": prereg.strategy,
        "version": prereg.version,
        "prereg_sha": prereg_sha,
        "commit": commit,
        "exploratory": exploratory,
        "data_sha": dsha,
        "period": period,
        "verdict": _verdict([results[n]["outcome"] for n in names]),
        "sizing": {
            "capital_usd": sizing.capital_usd,
            "trade_usd": sizing.capital_usd * sizing.notional_mult,
            "max_positions": sizing.max_positions,
        },
        "targets": {
            "usd_day": float(fixed.get("target_usd_day", 200.0)),
            "max_drawdown_usd": float(fixed.get("max_drawdown_usd", 2500.0)),
            "trades_day": float(fixed.get("min_trades_day", 20.0)),
        },
        "configs": results,
        "program_trials": ledger.count(),
    }


def _verdict(outcomes: list[str]) -> str:
    """The best class any configuration reached (each is judged on its own)."""
    for k in ("HEDEF", "KARLI"):
        if k in outcomes:
            return k
    return "KANITSIZ"
