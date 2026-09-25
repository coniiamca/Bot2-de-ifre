"""Run a pre-registered intraday hypothesis (I1–I4): every trial of the grid on every symbol
of the point-in-time universe, portfolio limits across symbols, Tier-0 gates on daily net
returns, ledger and report. The coin pool size (top 5 / 10 / 20 of each month) is a grid
dimension like any other. Development runs never load the lockbox period.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from quanta.core.log import get_logger
from quanta.research.costs import CostModel
from quanta.research.data import load_market
from quanta.research.fetch import symbol_windows
from quanta.research.gates import (
    Alpha,
    FamilyStats,
    Gate,
    alpha_vs,
    annual_sr,
    cagr,
    family_stats,
    max_drawdown,
    plateau,
    share_positive,
)
from quanta.research.intraday import (
    REASONS,
    IntradayCosts,
    Portfolio,
    Trades,
    combine,
    daily_pnl,
    run_trades,
)
from quanta.research.intraday_strategies import prepare, signals
from quanta.research.ledger import Ledger, trial_id
from quanta.research.minute import load_bars
from quanta.research.prereg import Prereg, git_state
from quanta.research.runner import _day, _iso, _neighbours, _prior_returns, _run_trial
from quanta.research.strategies import TrendParams
from quanta.research.universe import read_universe

log = get_logger(__name__)
Floats = NDArray[np.float64]
DATA_MANIFESTS = (
    Path("research/data/manifest_1m.csv.gz"),
    Path("research/data/manifest_top20_1h.csv.gz"),
)
MIN_TRADES = 300
Key = tuple[int, int, str]  # (combo without pool, pool size, variant)


def data_sha(repo: Path) -> str:
    h = hashlib.sha256()
    for m in DATA_MANIFESTS:
        p = repo / m
        h.update(p.read_bytes() if p.exists() else b"none")
    return h.hexdigest()[:16]


def costs_of(prereg: Prereg) -> IntradayCosts:
    c = prereg.costs
    return IntradayCosts(
        taker_bps=c.fee_bps,
        maker_bps=c.maker_bps,
        slip_bps=(c.slip_top2_bps, c.slip_rest_bps, c.slip_6_10_bps, c.slip_11_20_bps),
        stop_slip_mult=c.stop_slip_mult,
    )


def portfolio_of(fixed: dict[str, Any]) -> Portfolio:
    return Portfolio(
        risk_per_trade=float(fixed.get("risk_per_trade", 0.005)),
        cap_symbol=float(fixed.get("cap_symbol", 1.0)),
        cap_gross=float(fixed.get("cap_gross", 1.5)),
        max_positions=int(fixed.get("max_positions", 3)),
    )


def split_grid(prereg: Prereg) -> tuple[list[dict[str, Any]], list[int], list[tuple[int, int]]]:
    """Combos without the pool, the pool sizes, and for each full trial (prereg order) its
    (combo index, pool)."""
    grid = {k: v for k, v in prereg.grid.items() if k != "pool"}
    pools = [int(x) for x in prereg.grid.get("pool", [10**4])]
    keys = list(grid)
    base = [dict(zip(keys, vals, strict=True)) for vals in itertools.product(*grid.values())]
    index = {tuple(c[k] for k in keys): i for i, c in enumerate(base)}
    trials = [
        (index[tuple(c[k] for k in keys)], int(c.get("pool", pools[0]))) for c in prereg.combos()
    ]
    return base, pools, trials


def _symbol_job(args: tuple[Any, ...]) -> tuple[str, dict[Key, Trades]]:
    root, universe, symbol, start, end, strategy, combos, pools, fixed, variants = args
    out: dict[Key, Trades] = {}
    # the months the minute fetch downloaded for this universe (1 before, 0 after)
    bars = load_bars(root, universe, symbol, start, end, symbol_windows(universe, 1, 0).get(symbol))
    if bars is None or not (bars.rank > 0).any():
        return symbol, out
    prep = prepare(bars, fixed)
    for ci, g in combos:
        sig = signals(strategy, prep, g, fixed)
        for name, (costs, latency) in variants.items():
            for pool in pools:
                out[(ci, pool, name)] = run_trades(bars, sig, costs, max_rank=pool, latency=latency)
    return symbol, out


def simulate_all(
    root: Path,
    universe: list[tuple[str, int, str]],
    start: str,
    end: str,
    strategy: str,
    combos: list[tuple[int, dict[str, Any]]],
    pools: list[int],
    fixed: dict[str, Any],
    variants: dict[str, tuple[IntradayCosts, int]],
    workers: int | None = None,
) -> tuple[list[str], dict[Key, Trades]]:
    """Trades of every (combo, pool, variant), all symbols concatenated (symbol = index)."""
    symbols = sorted({s for _, _, s in universe})
    jobs = [
        (root, universe, s, start, end, strategy, combos, pools, fixed, variants) for s in symbols
    ]
    n = workers if workers is not None else min(4, os.cpu_count() or 1)
    if n > 1:
        # spawn, not fork: the parent has threads (pyarrow, logging) and fork could deadlock
        ctx = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=n, mp_context=ctx) as ex:
            results = list(ex.map(_symbol_job, jobs))
    else:
        results = [_symbol_job(j) for j in jobs]
    parts: dict[Key, list[Trades]] = {}
    for si, (_, res) in enumerate(results):
        for key, tr in res.items():
            parts.setdefault(key, []).append(tr.with_symbol(si))
    return symbols, {k: Trades.concat(v) for k, v in parts.items()}


def _stats(tr: Trades, w: Floats, days: int) -> dict[str, Any]:
    n = len(tr)
    if n == 0:
        return {"trades": 0, "per_day": 0.0}
    net = tr.net
    win = net > 0
    cost = tr.fee + tr.slip
    before = tr.gross + tr.slip  # return at quoted prices, before fees and slippage
    return {
        "trades": n,
        "per_day": n / max(days, 1),
        "win_rate": float(win.mean()),
        "avg_win": float(net[win].mean()) if win.any() else 0.0,
        "avg_loss": float(net[~win].mean()) if (~win).any() else 0.0,
        "avg_net": float(net.mean()),
        "avg_cost": float(cost.mean()),
        "avg_before_cost": float(before.mean()),
        "avg_minutes": float(np.mean((tr.exit_t - tr.entry_t) / 60e9)),
        "long_share": float(np.mean(tr.side > 0)),
        "reasons": {REASONS[r]: int(np.sum(tr.reason == r)) for r in range(len(REASONS))},
        "avg_weight": float(np.mean(w)),
        "liquidation_flags": int(tr.liq.sum()),
    }


def _yearly(first_day: int, daily: Floats) -> dict[str, float]:
    years = np.array([_iso(first_day + d)[:4] for d in range(daily.size)])
    return {y: float(np.prod(1.0 + daily[years == y]) - 1.0) for y in sorted(set(years))}


def _window(first_day: int, daily: Floats, lo: str, hi: str) -> float | None:
    d = np.arange(daily.size) + first_day
    sel = (d >= _day(lo)) & (d < _day(hi))
    return float(np.prod(1.0 + daily[sel]) - 1.0) if sel.any() else None


def run_intraday(
    prereg: Prereg,
    prereg_sha: str,
    root: Path,
    repo: Path,
    *,
    final: bool = False,
    exploratory: bool = False,
    workers: int | None = None,
) -> dict[str, Any]:
    universe = read_universe(repo / prereg.universe_file)
    costs = costs_of(prereg)
    pf = portfolio_of(prereg.fixed)
    v = prereg.validation
    combos, pools, trial_map = split_grid(prereg)
    first_day = _day(prereg.data_start) + prereg.warmup_days
    end_day = _day(prereg.dev_end)
    n_days = end_day - first_day
    symbols, trades = simulate_all(
        root,
        universe,
        prereg.data_start,
        prereg.dev_end,
        prereg.strategy,
        list(enumerate(combos)),
        pools,
        prereg.fixed,
        {"base": (costs, 0)},
        workers,
    )
    full = prereg.combos()
    names = [_name(c) for c in full]
    accepted: list[tuple[Trades, Floats]] = []
    cols = []
    for ci, pool in trial_map:
        tr, w = combine(trades.get((ci, pool, "base"), Trades.concat([])), pf)
        accepted.append((tr, w))
        cols.append(daily_pnl(tr, w, first_day, end_day))
        log.info("trial_done", trial=names[len(cols) - 1], trades=len(tr))
    R = np.column_stack(cols)
    commit, dirty = git_state(repo)
    exploratory = exploratory or dirty
    dsha = data_sha(repo)
    period = f"{prereg.data_start}..{prereg.dev_end}"
    ledger = Ledger(repo)
    day_axis = np.arange(first_day, end_day, dtype=np.int64)
    prior, prior_summary = _prior_returns(prereg, ledger, period, dsha, day_axis, _name)
    fs = family_stats(R, names, v.hold_days, v.lookback_days, prior)
    best_ci, best_pool = trial_map[fs.best]
    best_tr, best_w = accepted[fs.best]
    best_daily = R[:, fs.best]
    # second pass: sensitivities of the selected trial only
    variants = {
        "maliyet ×1,5": (costs.but(stress=1.5), 0),
        "maliyet ×2": (costs.but(stress=2.0), 0),
        "1 dakika gecikme": (costs, 1),
        "limit için +1 bps geçiş şartı": (costs.but(through_bps=1.0), 0),
        "BNB indirimi (−%10 ücret)": (costs.but(fee_mult=0.9), 0),
    }
    _, sens_trades = simulate_all(
        root,
        universe,
        prereg.data_start,
        prereg.dev_end,
        prereg.strategy,
        [(best_ci, combos[best_ci])],
        [best_pool],
        prereg.fixed,
        variants,
        workers,
    )
    sens: dict[str, float] = {}
    for name in variants:
        tr, w = combine(sens_trades.get((best_ci, best_pool, name), Trades.concat([])), pf)
        sens[name] = annual_sr(daily_pnl(tr, w, first_day, end_day))
    for label, side in (("yalnız long", 1), ("yalnız short", -1)):
        sub = trades.get((best_ci, best_pool, "base"), Trades.concat([]))
        tr, w = combine(sub.take(np.flatnonzero(sub.side == side)), pf)
        sens[label] = annual_sr(daily_pnl(tr, w, first_day, end_day))
    bench = benchmark_daily(
        root, universe, best_pool, prereg.data_start, prereg.dev_end, first_day, end_day
    )
    alpha = alpha_vs(best_daily, bench, v.hold_days)
    contrib = contributions(best_tr, best_w, symbols)
    yearly = _yearly(first_day, best_daily)
    stats = _stats(best_tr, best_w, n_days)
    gates = tier0_gates(
        fs,
        full,
        prereg.grid,
        sens["maliyet ×1,5"],
        sens["1 dakika gecikme"],
        contrib,
        yearly,
        alpha,
        stats,
    )
    passed = all(g.passed for g in gates)
    enough = stats["trades"] >= MIN_TRADES
    verdict = "GECTI" if passed else ("ELENDI" if enough else "SONUCSUZ")
    base = {
        "hypothesis": prereg.id,
        "version": prereg.version,
        "prereg_sha": prereg_sha,
        "commit": commit,
        "exploratory": exploratory,
        "data_sha": dsha,
        "period": period,
    }
    for j, c in enumerate(full):
        ci, pool = trial_map[j]
        ledger.record_trial(
            base,
            trial_id(prereg.id, prereg.version, c, dsha, period),
            c,
            day_axis,
            R[:, j],
            {
                "sr_annual": round(annual_sr(R[:, j]), 4),
                "days": int(n_days),
                "trades": len(accepted[j][0]),
            },
        )
    pool_cmp: dict[str, Any] = {}
    for pool in pools:
        idx = [j for j, (_, p) in enumerate(trial_map) if p == pool]
        jb = max(idx, key=lambda j: fs.sr_annual[j])
        tr, w = accepted[jb]
        st = _stats(tr, w, n_days)
        pool_cmp[str(pool)] = {
            "best": names[jb],
            "sr_annual": fs.sr_annual[jb],
            "cagr": cagr(R[:, jb]),
            "max_drawdown": max_drawdown(R[:, jb]),
            "trades": st["trades"],
            "avg_net": st.get("avg_net", 0.0),
            "avg_cost": st.get("avg_cost", 0.0),
        }
    doc: dict[str, Any] = {
        "hypothesis": prereg.id,
        "title": prereg.title,
        "strategy": prereg.strategy,
        "version": prereg.version,
        "prereg_sha": prereg_sha,
        "commit": commit,
        "exploratory": exploratory,
        "data_sha": dsha,
        "period": period,
        "verdict": verdict,
        "symbols": len({symbols[i] for i in np.unique(best_tr.symbol)}) if len(best_tr) else 0,
        "universe_top": best_pool,
        "family": fs.as_dict(),
        "prior": prior_summary,
        "gates": [asdict(g) for g in gates],
        "best": {
            "name": names[fs.best],
            "params": full[fs.best],
            "sr_annual": annual_sr(best_daily),
            "cagr": cagr(best_daily),
            "max_drawdown": max_drawdown(best_daily),
            "vol_annual": float(np.std(best_daily, ddof=1) * math.sqrt(365)),
            "yearly": yearly,
            "trades": stats,
        },
        "benchmark": {
            "sr_annual": annual_sr(bench),
            "cagr": cagr(bench),
            "max_drawdown": max_drawdown(bench),
            "yearly": _yearly(first_day, bench),
        },
        "alpha": asdict(alpha),
        "sensitivity_sr_annual": sens,
        "pools": pool_cmp,
        "stress": {
            name: {
                "strateji": _window(first_day, best_daily, lo, hi),
                "al-tut": _window(first_day, bench, lo, hi),
            }
            for lo, hi, name in prereg.stress_windows
        },
        "asset_contribution": dict(sorted(contrib.items(), key=lambda kv: kv[1])),
        "program_trials": ledger.count(),
        "lockbox": None,
    }
    if final:
        if not passed:
            raise RuntimeError("development gates failed: the lockbox stays closed")
        ledger.open_lockbox(base | {"best": names[fs.best]})
        lock_end = _day(prereg.lockbox_end)
        _, lt = simulate_all(
            root,
            universe,
            prereg.data_start,
            prereg.lockbox_end,
            prereg.strategy,
            [(best_ci, combos[best_ci])],
            [best_pool],
            prereg.fixed,
            {"base": (costs, 0)},
            workers,
        )
        tr, w = combine(lt.get((best_ci, best_pool, "base"), Trades.concat([])), pf)
        lock = daily_pnl(tr, w, end_day, lock_end)
        lsr = annual_sr(lock)
        p5 = float(np.percentile(fs.cpcv_path_sr_annual, 5))
        doc["lockbox"] = {
            "days": int(lock.size),
            "sr_annual": lsr,
            "return": float(np.prod(1.0 + lock) - 1.0),
            "cpcv_p5": p5,
            "passed": lsr > 0 and lsr >= p5,
        }
        doc["verdict"] = "GECTI" if doc["lockbox"]["passed"] else "ELENDI"
    return doc


def benchmark_daily(
    root: Path,
    universe: list[tuple[str, int, str]],
    pool: int,
    data_start: str,
    dev_end: str,
    first_day: int,
    end_day: int,
) -> Floats:
    """Daily returns of an equal-risk buy-and-hold of the same coin pool (hourly, as in H6)."""
    pool_universe = [u for u in universe if u[1] <= pool]
    m = load_market(
        root,
        pool_universe,
        data_start,
        dev_end,
        metrics=False,
        windows=symbol_windows(universe),  # hourly data fetched for the whole universe
    )
    bdays, bench_d, _ = _run_trial(
        m, TrendParams(168, 24, "sign", long_only=True), CostModel(), first_day
    )
    bench = np.zeros(end_day - first_day)
    sel = (bdays >= first_day) & (bdays < end_day)
    bench[bdays[sel] - first_day] = bench_d[sel]
    return bench


def contributions(tr: Trades, w: Floats, symbols: list[str]) -> dict[str, float]:
    """Net contribution of each coin that carried at least 1 % of the traded weight."""
    if not len(tr):
        return {}
    pnl = w * tr.net
    weight = np.bincount(tr.symbol, weights=w, minlength=len(symbols))
    by_sym = np.bincount(tr.symbol, weights=pnl, minlength=len(symbols))
    share = weight / max(weight.sum(), 1e-12)
    return {s: float(by_sym[i]) for i, s in enumerate(symbols) if share[i] >= 0.01}


def tier0_gates(
    fs: FamilyStats,
    full: list[dict[str, Any]],
    grid: dict[str, list[Any]],
    cost_sr: float,
    latency_sr: float,
    contrib: dict[str, float],
    yearly: dict[str, float],
    alpha: Alpha,
    stats: dict[str, Any],
    cost_rule: str = "maliyet ×1,5'te SR > 0",
) -> list[Gate]:
    """The Tier-0 gates of an intraday family (design §17.2)."""
    return [
        *fs.gates,
        plateau(fs.sr_annual, fs.best, _neighbours(full, grid, fs.best)),
        _gate("maliyet_x1_5", cost_sr > 0, cost_sr, cost_rule),
        _gate("gecikme", latency_sr > 0, latency_sr, "emir 1 dk geç gitse de SR > 0"),
        Gate(
            "varlik_degismezlik",
            share_positive(list(contrib.values())) >= 2 / 3,
            f"%{share_positive(list(contrib.values())) * 100:.0f} pozitif ({len(contrib)} coin)",
            "coinlerin ≥ 2/3'ünde pozitif katkı",
        ),
        Gate(
            "yil_degismezlik",
            share_positive(list(yearly.values())) > 0.5,
            f"%{share_positive(list(yearly.values())) * 100:.0f} pozitif ({len(yearly)} yıl)",
            "yılların çoğunda pozitif",
        ),
        Gate(
            "alfa",
            alpha.alpha_annual > 0 and alpha.t_nw >= 2.0,
            f"yıllık %{alpha.alpha_annual * 100:.1f}, t {alpha.t_nw:.2f}, beta {alpha.beta:.2f}",
            "al-tut kıyasına göre alfa > 0 ve t ≥ 2",
        ),
        Gate(
            "likidasyon",
            stats.get("liquidation_flags", 0) == 0,
            str(stats.get("liquidation_flags", 0)),
            "hiçbir işlemde %30 ters hareket yok",
        ),
        Gate(
            "islem_sayisi",
            stats["trades"] >= MIN_TRADES,
            str(stats["trades"]),
            f"en az {MIN_TRADES} işlem (daha azı: sonuçsuz)",
        ),
    ]


def _gate(name: str, ok: bool, sr: float, rule: str) -> Gate:
    return Gate(name, ok, f"SR {sr:.2f}", rule)


def _name(c: dict[str, Any]) -> str:
    return "_".join(f"{k}{v}" for k, v in c.items())
