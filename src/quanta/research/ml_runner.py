"""Run model M1 (``research/prereg/M1.yaml``): walk-forward forecasts on USDT perpetual data,
maker-first trades priced with the USDC-margined fee schedule, Tier-0 gates on daily net
returns, and the USDC reality check that replays the selected trades on the USDC contracts.

Steps:

1. features of every symbol that was ever in the top ``top`` (:mod:`quanta.research.features`),
   joined into one frame of the universe rows;
2. monthly walk-forward forecasts per horizon and model (:mod:`quanta.research.ml`);
3. entries where a forecast passes its month's calibrated threshold, only on tradable rows:
   the top ``top`` and — from ``usdc_from`` — only coins whose USDC contract was listed at
   least one full month earlier and traded enough the month before;
4. the minute engine simulates each trial (post-only entry, maker time exit, stop-market) on
   the USDT bars; portfolio limits; daily returns; gates;
5. the selected trial again on the real USDC bars (2024+), which must hold up.

Development runs never fit or trade the lockbox months.
"""

from __future__ import annotations

import hashlib
import math
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from quanta.core.log import get_logger
from quanta.research.data import _read
from quanta.research.features import (
    Frame,
    assemble,
    metrics_series,
    premium_series,
    symbol_frame,
)
from quanta.research.fetch import USDC_START, symbol_windows
from quanta.research.gates import (
    Gate,
    alpha_vs,
    annual_sr,
    cagr,
    family_stats,
    max_drawdown,
)
from quanta.research.intraday import (
    IntradayCosts,
    Portfolio,
    Signals,
    Trades,
    combine,
    daily_pnl,
    run_trades,
)
from quanta.research.intraday_runner import (
    MIN_TRADES,
    _stats,
    _window,
    _yearly,
    benchmark_daily,
    contributions,
    costs_of,
    portfolio_of,
    tier0_gates,
)
from quanta.research.ledger import Ledger, trial_id
from quanta.research.minute import MIN_NS, Bars, fill_gaps, load_bars, month_of
from quanta.research.ml import Forecast, ModelSpec, entries, walk_forward
from quanta.research.prereg import Prereg, git_state
from quanta.research.runner import _day, _iso
from quanta.research.universe import month_range, read_universe

log = get_logger(__name__)
Floats = NDArray[np.float64]
DATA_MANIFESTS = (
    Path("research/data/manifest_1m.csv.gz"),
    Path("research/data/manifest_top20_1h.csv.gz"),
    Path("research/data/manifest_m1.csv.gz"),
    Path("research/data/manifest_usdc_1m.csv.gz"),
)
LEAK_SR = 10.0  # an annual Sharpe above this after costs means a data leak, not a discovery
Order = tuple[NDArray[np.int64], NDArray[np.int8], Floats, int]  # decision t, side, σ, horizon


def data_sha(repo: Path, manifests: list[str] | None = None) -> str:
    h = hashlib.sha256()
    for m in manifests or [str(p) for p in DATA_MANIFESTS]:
        p = repo / m
        h.update(p.read_bytes() if p.exists() else b"none")
    return h.hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class Settings:
    top: int
    ml_start: str  # first test month
    entry_valid: int
    exit_valid: int
    stop_k: float  # stop at stop_k · σ(1 min) · √h
    usdc_first_month: str  # first month of USDC perpetuals in the archive
    usdc_from: str
    usdc_min_qv_day: float
    usdc_gap_fill: int
    usdc_check_start: str
    recent_start: str
    prior_trial_count: int
    spec: dict[str, Any]

    @staticmethod
    def of(fixed: dict[str, Any]) -> Settings:
        return Settings(
            top=int(fixed.get("top", 10)),
            ml_start=str(fixed.get("ml_start", "2021-01")),
            entry_valid=int(fixed.get("entry_valid", 5)),
            exit_valid=int(fixed.get("exit_valid", 5)),
            stop_k=float(fixed.get("stop_k", 3.0)),
            usdc_first_month=str(fixed.get("usdc_first_month", USDC_START)),
            usdc_from=str(fixed.get("usdc_from", "2024-02")),
            usdc_min_qv_day=float(fixed.get("usdc_min_qv_day", 5e6)),
            usdc_gap_fill=int(fixed.get("usdc_gap_fill", 60)),
            usdc_check_start=str(fixed.get("usdc_check_start", "2024-01-01")),
            recent_start=str(fixed.get("recent_start", "2025-01-01")),
            prior_trial_count=int(fixed.get("prior_trial_count", 0)),
            spec=dict(fixed.get("model", {})),
        )

    def model(self, kind: str, horizon: int) -> ModelSpec:
        s = self.spec
        base = ModelSpec(kind)
        params = dict(base.params, **s.get("lgbm", {}))
        return ModelSpec(
            kind,
            train_days=int(s.get("train_days", base.train_days)),
            calib_days=int(s.get("calib_days", base.calib_days)),
            gap_days=int(s.get("gap_days", base.gap_days)),
            halflife_days=float(s.get("halflife_days", base.halflife_days)),
            rounds=int(s.get("rounds", base.rounds)),
            ridge_lambda=float(s.get("ridge_lambda", base.ridge_lambda)),
            params=params,
            label_span_min=horizon + 1 + self.entry_valid + self.exit_valid + 1,
            min_train_rows=int(s.get("min_train_rows", base.min_train_rows)),
        )


def usdc_symbol(usdt: str) -> str:
    return usdt.removesuffix("USDT") + "USDC"


# ---------------------------------------------------------------- features (per symbol)


def _frame_job(args: tuple[Any, ...]) -> tuple[int, Frame | None]:
    root, universe, symbol, idx, start, end, horizons, top = args
    window = symbol_windows(universe, 1, 0).get(symbol)
    bars = load_bars(root, universe, symbol, start, end, window)
    if bars is None:
        return idx, None
    top_rows = [u for u in universe if u[1] <= top]
    w_top = symbol_windows(top_rows, 1, 0).get(symbol, window)
    prem = premium_series(
        _read(root, "premiumIndexKlines_5m", symbol, ["open_time", "close"], w_top)
    )
    cols = [
        "create_time",
        "sum_open_interest",
        "sum_toptrader_long_short_ratio",
        "count_long_short_ratio",
        "sum_taker_long_short_vol_ratio",
    ]
    met = metrics_series(_read(root, "metrics", symbol, cols, w_top))
    f = symbol_frame(bars, idx, horizons, prem, met)
    keep = np.flatnonzero((f.rank >= 1) & (f.rank <= top)).astype(np.int64)
    return idx, f.take(keep) if keep.size else None


def build_frame(
    root: Path,
    universe: list[tuple[str, int, str]],
    top: int,
    start: str,
    end: str,
    horizons: tuple[int, ...],
    workers: int | None = None,
) -> tuple[Frame, list[str]]:
    symbols = sorted({s for _, r, s in universe if r <= top})
    jobs = [(root, universe, s, i, start, end, horizons, top) for i, s in enumerate(symbols)]
    out = _pool(_frame_job, jobs, workers)
    frames = {i: f for i, f in out if f is not None}
    btc = frames.get(symbols.index("BTCUSDT")) if "BTCUSDT" in symbols else None
    return assemble(list(frames.values()), btc), symbols


def _pool(fn: Any, jobs: list[Any], workers: int | None) -> list[Any]:
    n = workers if workers is not None else min(4, os.cpu_count() or 1)
    if n > 1 and len(jobs) > 1:
        # spawn, not fork: the parent has threads (pyarrow, lightgbm) and fork could deadlock
        ctx = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=n, mp_context=ctx) as ex:
            return list(ex.map(fn, jobs))
    return [fn(j) for j in jobs]


# ---------------------------------------------------------------- USDC eligibility


def _usdc_volume_job(args: tuple[Any, ...]) -> tuple[str, dict[str, float]]:
    root, symbol, lo, hi = args
    t = _read(root, "klines_1m", symbol, ["open_time", "quote_volume"], (lo, hi))
    if t is None:
        return symbol, {}
    ot = np.asarray(t.column("open_time").cast("int64").to_numpy(), dtype=np.int64)
    qv = np.asarray(t.column("quote_volume").to_numpy(), dtype=np.float64)
    months = month_of(ot)
    out: dict[str, float] = {}
    for m in np.unique(months):
        sel = months == m
        days = max(len(np.unique(ot[sel] // (86400 * 10**9))), 1)
        out[str(m)] = float(qv[sel].sum()) / days
    return symbol, out


def usdc_eligibility(
    root: Path, symbols: list[str], st: Settings, last_month: str, workers: int | None = None
) -> dict[tuple[str, str], bool]:
    """(month, USDT symbol) → the USDC contract was listed at least one full month earlier
    and traded ``usdc_min_qv_day`` a day on average in the previous month."""
    lo = st.usdc_first_month
    jobs = [(root, usdc_symbol(s), lo, last_month) for s in symbols]
    vols = dict(_pool(_usdc_volume_job, jobs, workers))
    out: dict[tuple[str, str], bool] = {}
    months = month_range(lo, last_month)
    for s in symbols:
        per = vols.get(usdc_symbol(s), {})
        if not per:
            continue
        first = min(per)
        for k, m in enumerate(months):
            if k < 1:
                continue
            prev = months[k - 1]
            listed = months.index(first) + 2 <= k
            out[(m, s)] = listed and per.get(prev, 0.0) >= st.usdc_min_qv_day
    return out


# ---------------------------------------------------------------- simulation (per symbol)


def _signals(bars: Bars, order: Order, st: Settings, mode: str) -> Signals:
    """Signals of one symbol: entry at the decision bar's close of ``bars`` (USDT, or the
    USDC contract in the reality check), stop at ``stop_k`` · σ · √h of that price, where σ
    is the forecast's (USDT) volatility."""
    t_dec, side, sigma, h = order
    bar_t = t_dec - MIN_NS
    i = np.searchsorted(bars.t, bar_t)
    ok = (i < len(bars)) & (bars.t[np.minimum(i, len(bars) - 1)] == bar_t)
    i, side, sigma = i[ok].astype(np.int64), side[ok], sigma[ok]
    px = bars.c[i]
    stop = px * (1.0 - side * st.stop_k * sigma * math.sqrt(h))
    limit = np.full(i.size, np.nan) if mode == "market" else px
    return Signals.build(
        i,
        side,
        limit,
        stop,
        np.full(i.size, np.nan),
        h,
        st.entry_valid,
        post_only=mode != "market",
        exit_valid=st.exit_valid,
    )


def _sim_job(args: tuple[Any, ...]) -> tuple[str, dict[tuple[str, str], Trades]]:
    root, universe, symbol, start, end, orders, variants, st, usdc = args
    out: dict[tuple[str, str], Trades] = {}
    if usdc:
        window = (st.usdc_first_month, end[:7])
        bars = load_bars(root, universe, usdc_symbol(symbol), start, end, window, rank_of=symbol)
        if bars is not None:
            bars = fill_gaps(bars, st.usdc_gap_fill)
    else:
        bars = load_bars(
            root, universe, symbol, start, end, symbol_windows(universe, 1, 0).get(symbol)
        )
    if bars is None:
        return symbol, out
    for key, order in orders.items():
        for name, (costs, latency, mode) in variants.items():
            sig = _signals(bars, order, st, mode)
            out[(key, name)] = run_trades(bars, sig, costs, latency=latency)
    return symbol, out


def simulate(
    root: Path,
    universe: list[tuple[str, int, str]],
    symbols: list[str],
    start: str,
    end: str,
    orders: dict[str, dict[int, Order]],
    variants: dict[str, tuple[IntradayCosts, int, str]],
    st: Settings,
    *,
    usdc: bool = False,
    workers: int | None = None,
) -> dict[tuple[str, str], Trades]:
    """``orders``: trial key → symbol index → order rows. Trades of every (trial, variant),
    all symbols concatenated (``symbol`` = index into ``symbols``)."""
    per_sym: dict[int, dict[str, Order]] = {}
    for key, by_sym in orders.items():
        for si, o in by_sym.items():
            per_sym.setdefault(si, {})[key] = o
    jobs = [
        (root, universe, symbols[si], start, end, o, variants, st, usdc)
        for si, o in sorted(per_sym.items())
    ]
    results = _pool(_sim_job, jobs, workers)
    parts: dict[tuple[str, str], list[Trades]] = {}
    for (si, _), (_, res) in zip(sorted(per_sym.items()), results, strict=True):
        for k, tr in res.items():
            parts.setdefault(k, []).append(tr.with_symbol(si))
    return {k: Trades.concat(v) for k, v in parts.items()}


def split_orders(
    frame: Frame, rows: NDArray[np.int64], side: NDArray[np.int8], h: int
) -> dict[int, Order]:
    out: dict[int, Order] = {}
    sym = frame.sym[rows]
    for s in np.unique(sym):
        sel = rows[sym == s]
        out[int(s)] = (frame.t[sel], side[sym == s], frame.sigma[sel], h)
    return out


# ---------------------------------------------------------------- the run


def _name(c: dict[str, Any]) -> str:
    return "_".join(f"{k}{v}" for k, v in c.items())


def _costs_usdt(c: IntradayCosts) -> IntradayCosts:
    return IntradayCosts(
        taker_bps=5.0, maker_bps=2.0, slip_bps=(1.0, 3.0, 5.0, 8.0), stop_slip_mult=c.stop_slip_mult
    )


def _break_even_maker_bps(tr: Trades, w: Floats, taker: float) -> float | None:
    """Maker fee (bps) at which the trades' total net return is zero."""
    if not len(tr) or taker <= 0:
        return None
    taker_legs = np.rint(tr.fee / taker)
    maker_weight = float(np.sum(w * (2.0 - taker_legs)))
    if maker_weight <= 0:
        return None
    return float(np.sum(w * tr.net) / maker_weight * 1e4)


def _sr_between(first_day: int, daily: Floats, lo: str, hi: str) -> float:
    d = np.arange(daily.size) + first_day
    sel = (d >= _day(lo)) & (d < _day(hi))
    return annual_sr(daily[sel]) if sel.sum() > 30 else 0.0


def run_ml(
    prereg: Prereg,
    prereg_sha: str,
    root: Path,
    repo: Path,
    *,
    final: bool = False,
    exploratory: bool = False,
    workers: int | None = None,
    shuffle_seed: int | None = None,
) -> dict[str, Any]:
    """``shuffle_seed``: timing run on permuted labels (always exploratory, nothing learned)."""
    st = Settings.of(prereg.fixed)
    universe = read_universe(repo / prereg.universe_file)
    costs = costs_of(prereg)
    pf: Portfolio = portfolio_of(prereg.fixed)
    v = prereg.validation
    full = prereg.combos()
    names = [_name(c) for c in full]
    horizons = tuple(sorted({int(c["horizon"]) for c in full}))
    qs = tuple(sorted({float(c["q"]) for c in full}))
    kinds = sorted({str(c["model"]) for c in full})
    first_day = _day(st.ml_start + "-01")
    end_day = _day(prereg.dev_end)
    n_days = end_day - first_day
    dev_months = month_range(st.ml_start, _iso(end_day - 1)[:7])
    lock_months = month_range(prereg.dev_end[:7], _iso(_day(prereg.lockbox_end) - 1)[:7])
    data_end = prereg.lockbox_end if final else prereg.dev_end
    frame, symbols = build_frame(
        root, universe, st.top, prereg.data_start, data_end, horizons, workers
    )
    months = month_of(frame.t - MIN_NS)  # the month of the decision bar
    last_month = _iso(_day(data_end) - 1)[:7]
    eligible = usdc_eligibility(root, symbols, st, last_month, workers)
    sym_names = np.array(symbols)[frame.sym]
    usdc_ok = np.array(
        [eligible.get((str(m), str(s)), False) for m, s in zip(months, sym_names, strict=True)]
    )
    tradable = (frame.rank >= 1) & (frame.rank <= st.top) & ((months < st.usdc_from) | usdc_ok)
    train_rows = (frame.rank >= 1) & (frame.rank <= st.top)
    log.info("ml_frame", rows=len(frame), tradable=int(tradable.sum()), symbols=len(symbols))
    forecasts: dict[tuple[int, str], Forecast] = {}
    for h in horizons:
        for kind in kinds:
            forecasts[(h, kind)] = walk_forward(
                frame, h, st.model(kind, h), dev_months, train_rows, qs, shuffle_seed=shuffle_seed
            )
    orders: dict[str, dict[int, Order]] = {}
    for c, name in zip(full, names, strict=True):
        h, q, kind = int(c["horizon"]), float(c["q"]), str(c["model"])
        rows, side = entries(frame, forecasts[(h, kind)], q, tradable, months)
        orders[name] = split_orders(frame, rows, side, h)
    base_v = {"base": (costs, 0, "maker")}
    trades = simulate(
        root,
        universe,
        symbols,
        prereg.data_start,
        prereg.dev_end,
        orders,
        base_v,
        st,
        workers=workers,
    )
    accepted: list[tuple[Trades, Floats]] = []
    cols = []
    for name in names:
        tr, w = combine(trades.get((name, "base"), Trades.concat([])), pf)
        accepted.append((tr, w))
        cols.append(daily_pnl(tr, w, first_day, end_day))
    R = np.column_stack(cols)
    commit, dirty = git_state(repo)
    exploratory = exploratory or dirty or shuffle_seed is not None
    dsha = data_sha(repo, prereg.data_manifests or None)
    period = f"{st.ml_start}-01..{prereg.dev_end}"
    ledger = Ledger(repo)
    day_axis = np.arange(first_day, end_day, dtype=np.int64)
    fs = family_stats(R, names, v.hold_days, v.lookback_days, prior_count=st.prior_trial_count)
    best = fs.best
    best_name, best_c = names[best], full[best]
    best_tr, best_w = accepted[best]
    best_daily = R[:, best]
    h_best, kind_best = int(best_c["horizon"]), str(best_c["model"])
    # sensitivities of the selected trial
    variants = {
        "maliyet ×1,5 ve +1 bps geçiş": (costs.but(stress=1.5, through_bps=1.0), 0, "maker"),
        "1 dakika gecikme": (costs, 1, "maker"),
        "piyasa emriyle giriş": (costs, 0, "market"),
        "USDC standart ücret (promosyon biterse)": (
            costs.but(maker_bps=1.8, taker_bps=4.5),
            0,
            "maker",
        ),
        "USDT kontratı, VIP0 ücret": (_costs_usdt(costs), 0, "maker"),
    }
    sens_tr = simulate(
        root,
        universe,
        symbols,
        prereg.data_start,
        prereg.dev_end,
        {best_name: orders[best_name]},
        variants,
        st,
        workers=workers,
    )
    sens: dict[str, float] = {}
    for vname in variants:
        tr, w = combine(sens_tr.get((best_name, vname), Trades.concat([])), pf)
        sens[vname] = annual_sr(daily_pnl(tr, w, first_day, end_day))
    raw = trades.get((best_name, "base"), Trades.concat([]))
    for label, s in (("yalnız long", 1), ("yalnız short", -1)):
        tr, w = combine(raw.take(np.flatnonzero(raw.side == s)), pf)
        sens[label] = annual_sr(daily_pnl(tr, w, first_day, end_day))
    usdc = usdc_check(
        root,
        universe,
        symbols,
        frame,
        forecasts[(h_best, kind_best)],
        best_c,
        st,
        costs,
        pf,
        tradable & usdc_ok,
        months,
        prereg.dev_end,
        workers,
    )
    bench = benchmark_daily(
        root, universe, st.top, prereg.data_start, prereg.dev_end, first_day, end_day
    )
    alpha = alpha_vs(best_daily, bench, v.hold_days)
    contrib = contributions(best_tr, best_w, symbols)
    yearly = _yearly(first_day, best_daily)
    stats = _stats(best_tr, best_w, n_days)
    recent = _sr_between(first_day, best_daily, st.recent_start, prereg.dev_end)
    gates = tier0_gates(
        fs,
        full,
        prereg.grid,
        sens["maliyet ×1,5 ve +1 bps geçiş"],
        sens["1 dakika gecikme"],
        contrib,
        yearly,
        alpha,
        stats,
        cost_rule="ücret ve kayma ×1,5, limit emir 1 bps daha ötesine geçmeli: SR > 0",
    )
    gates += [
        Gate(
            "sizinti_bekcisi",
            fs.best_sr_annual < LEAK_SR,
            f"SR {fs.best_sr_annual:.2f}",
            f"yıllık Sharpe < {LEAK_SR:g} (daha yükseği gerçek değil, veri sızıntısı işaretidir)",
        ),
        Gate(
            "son_donem",
            recent > 0,
            f"SR {recent:.2f}",
            f"{st.recent_start[:7]} … {prereg.dev_end[:7]} (hariç) döneminde SR > 0",
        ),
        Gate(
            "usdc_kontrol",
            bool(usdc["passed"]),
            f"USDC SR {usdc['usdc_sr']:.2f} / vekil {usdc['proxy_sr']:.2f}, "
            f"dolum oranı {usdc['fill_ratio']:.2f}",
            "gerçek USDC mumlarında net > 0, SR ≥ vekilin yarısı, dolum oranı ±%30",
        ),
    ]
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
    if shuffle_seed is None:
        for j, c in enumerate(full):
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
    fc = forecasts[(h_best, kind_best)]
    top_features = dict(sorted(fc.importance.items(), key=lambda kv: -kv[1])[:12])
    by_model = {
        kind: max(
            (j for j, c in enumerate(full) if c["model"] == kind), key=lambda j: fs.sr_annual[j]
        )
        for kind in kinds
    }
    doc: dict[str, Any] = {
        "hypothesis": prereg.id,
        "title": prereg.title,
        "strategy": prereg.strategy,
        "version": prereg.version,
        "prereg_sha": prereg_sha,
        "commit": commit,
        "exploratory": exploratory,
        "timing_run": shuffle_seed is not None,
        "data_sha": dsha,
        "period": period,
        "verdict": verdict,
        "symbols": len({symbols[i] for i in np.unique(best_tr.symbol)}) if len(best_tr) else 0,
        "universe_top": st.top,
        "rows": {"all": len(frame), "tradable": int(tradable.sum())},
        "family": fs.as_dict(),
        "gates": [asdict(g) for g in gates],
        "best": {
            "name": best_name,
            "params": best_c,
            "sr_annual": annual_sr(best_daily),
            "cagr": cagr(best_daily),
            "max_drawdown": max_drawdown(best_daily),
            "vol_annual": float(np.std(best_daily, ddof=1) * math.sqrt(365)),
            "yearly": yearly,
            "trades": stats,
            "recent_sr": recent,
            "break_even_maker_bps": _break_even_maker_bps(best_tr, best_w, costs.taker),
        },
        "models": {
            kind: {"best": names[j], "sr_annual": fs.sr_annual[j]} for kind, j in by_model.items()
        },
        "features": top_features,
        "benchmark": {
            "sr_annual": annual_sr(bench),
            "cagr": cagr(bench),
            "max_drawdown": max_drawdown(bench),
            "yearly": _yearly(first_day, bench),
        },
        "alpha": asdict(alpha),
        "sensitivity_sr_annual": sens,
        "usdc_check": usdc,
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
        ledger.open_lockbox(base | {"best": best_name})
        doc["lockbox"] = lockbox(
            root,
            universe,
            symbols,
            frame,
            best_c,
            st,
            costs,
            pf,
            train_rows,
            tradable & usdc_ok,
            months,
            lock_months,
            prereg,
            fs.cpcv_path_sr_annual,
            workers,
        )
        doc["verdict"] = "GECTI" if doc["lockbox"]["passed"] else "ELENDI"
    return doc


def usdc_check(
    root: Path,
    universe: list[tuple[str, int, str]],
    symbols: list[str],
    frame: Frame,
    fc: Forecast,
    c: dict[str, Any],
    st: Settings,
    costs: IntradayCosts,
    pf: Portfolio,
    usdc_rows: NDArray[np.bool_],
    months: NDArray[np.str_],
    dev_end: str,
    workers: int | None,
) -> dict[str, Any]:
    """The selected trial's entries on coins with an eligible USDC contract, 2024+, run on
    the USDT bars (the proxy) and on the real USDC bars; the USDC result must hold up."""
    h, q = int(c["horizon"]), float(c["q"])
    first, end = _day(st.usdc_check_start), _day(dev_end)
    in_window = (frame.t >= first * 86400 * 10**9) & (frame.t < end * 86400 * 10**9)
    rows, side = entries(frame, fc, q, usdc_rows & in_window, months)
    orders = {"check": split_orders(frame, rows, side, h)}
    v = {"base": (costs, 0, "maker")}
    empty = Trades.concat([])
    args = (root, universe, symbols, st.usdc_check_start, dev_end, orders, v, st)
    proxy = simulate(*args, workers=workers).get(("check", "base"), empty)
    real = simulate(*args, usdc=True, workers=workers).get(("check", "base"), empty)
    out: dict[str, Any] = {"signals": int(rows.size)}
    daily = {}
    for label, tr in (("proxy", proxy), ("usdc", real)):
        acc, w = combine(tr, pf)
        d = daily_pnl(acc, w, first, end)
        daily[label] = d
        out[f"{label}_sr"] = annual_sr(d)
        out[f"{label}_return"] = float(np.prod(1.0 + d) - 1.0)
        out[f"{label}_fills"] = len(tr)
        out[f"{label}_trades"] = len(acc)
    fr_p = out["proxy_fills"] / max(rows.size, 1)
    fr_u = out["usdc_fills"] / max(rows.size, 1)
    out["fill_ratio"] = fr_u / fr_p if fr_p > 0 else 0.0
    out["daily_corr"] = (
        float(np.corrcoef(daily["proxy"], daily["usdc"])[0, 1])
        if np.std(daily["proxy"]) > 0 and np.std(daily["usdc"]) > 0
        else 0.0
    )
    out["passed"] = bool(
        out["usdc_return"] > 0
        and out["usdc_sr"] > 0
        and out["usdc_sr"] >= 0.5 * max(out["proxy_sr"], 0.0)
        and 0.7 <= out["fill_ratio"] <= 1.3
        and rows.size > 0
    )
    return out


def lockbox(
    root: Path,
    universe: list[tuple[str, int, str]],
    symbols: list[str],
    frame: Frame,
    c: dict[str, Any],
    st: Settings,
    costs: IntradayCosts,
    pf: Portfolio,
    train_rows: NDArray[np.bool_],
    usdc_rows: NDArray[np.bool_],
    months: NDArray[np.str_],
    lock_months: list[str],
    prereg: Prereg,
    cpcv_paths: list[float],
    workers: int | None,
) -> dict[str, Any]:
    """The selected trial once on the lockbox months, traded on the USDC contracts only."""
    h, q, kind = int(c["horizon"]), float(c["q"]), str(c["model"])
    fc = walk_forward(frame, h, st.model(kind, h), lock_months, train_rows, (q,))
    rows, side = entries(frame, fc, q, usdc_rows, months)
    orders = {"lock": split_orders(frame, rows, side, h)}
    tr = simulate(
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
    ).get(("lock", "base"), Trades.concat([]))
    acc, w = combine(tr, pf)
    lo, hi = _day(prereg.dev_end), _day(prereg.lockbox_end)
    d = daily_pnl(acc, w, lo, hi)
    lsr = annual_sr(d)
    p5 = float(np.percentile(cpcv_paths, 5))
    return {
        "days": int(d.size),
        "trades": len(acc),
        "sr_annual": lsr,
        "return": float(np.prod(1.0 + d) - 1.0),
        "cpcv_p5": p5,
        "passed": lsr > 0 and lsr >= p5,
    }
