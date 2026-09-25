"""Run a pre-registered hypothesis end to end: load the point-in-time panel, simulate every
trial of the grid, apply the Tier-0 gates, record all trials in the ledger and write the
report. Development runs never see the lockbox period; ``final=True`` opens it once, and
only for a family that passed its development gates.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from quanta.core.log import get_logger
from quanta.research.backtest import Result, simulate
from quanta.research.burst import add_burst_features
from quanta.research.costs import CostModel
from quanta.research.data import load_market
from quanta.research.gates import (
    Gate,
    alpha_vs,
    annual_sr,
    cagr,
    family_stats,
    max_drawdown,
    plateau,
    share_positive,
)
from quanta.research.ledger import Ledger, trial_id
from quanta.research.panel import DAY_NS, Market
from quanta.research.prereg import Prereg, git_state
from quanta.research.strategies import TrendParams, burst_weights, trend_weights
from quanta.research.universe import read_universe

log = get_logger(__name__)
Floats = NDArray[np.float64]
DATA_MANIFEST = Path("research/data/manifest.csv.gz")


@dataclass(slots=True)
class Trial:
    name: str
    params: dict[str, Any]
    days: NDArray[np.int64]
    daily: Floats
    result: Result


def _day(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=UTC).timestamp()) // 86400


def _iso(day: int) -> str:
    return date.fromordinal(date(1970, 1, 1).toordinal() + int(day)).isoformat()


def data_sha(repo: Path, manifests: list[str] | None = None) -> str:
    if not manifests:
        p = repo / DATA_MANIFEST
        return hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.exists() else "none"
    h = hashlib.sha256()
    for m in manifests:
        f = repo / m
        h.update(f.read_bytes() if f.exists() else b"none")
    return h.hexdigest()[:16]


def _weights(prereg: Prereg) -> Callable[[Market, TrendParams], Floats]:
    return burst_weights if prereg.strategy == "burst_imbalance" else trend_weights


def _market(prereg: Prereg, root: Path, universe: list[tuple[str, int, str]], end: str) -> Market:
    m = load_market(root, universe, prereg.data_start, end, metrics=False)
    if prereg.strategy == "burst_imbalance":
        windows = tuple(sorted({int(h) for h in prereg.grid["lookback_h"]}))
        add_burst_features(m, root, universe, prereg.data_start, end, windows)
    m.check_point_in_time()
    return m


def _trend_name(p: dict[str, Any]) -> str:
    return f"L{p['lookback_h']}_R{p['rebalance_h']}_{p['signal']}"


def _neighbours(combos: list[dict[str, Any]], grid: dict[str, list[Any]], best: int) -> list[int]:
    b = combos[best]
    out = []
    for i, c in enumerate(combos):
        diff = [k for k in grid if c[k] != b[k]]
        if len(diff) != 1:
            continue
        k = diff[0]
        vals = grid[k]
        if abs(vals.index(c[k]) - vals.index(b[k])) == 1:
            out.append(i)
    return out


def _run_trial(
    m: Market,
    p: TrendParams,
    costs: CostModel,
    first_day: int,
    weights: Callable[[Market, TrendParams], Floats] = trend_weights,
    **kw: Any,
) -> tuple[NDArray[np.int64], Floats, Result]:
    res = simulate(weights(m, p), m, costs, **kw)
    days, daily = res.daily(m.grid)
    keep = days >= first_day
    return days[keep], daily[keep], res


def _yearly(days: NDArray[np.int64], daily: Floats) -> dict[str, float]:
    years = np.array([_iso(d)[:4] for d in days])
    return {y: float(np.prod(1.0 + daily[years == y]) - 1.0) for y in sorted(set(years))}


def _window(days: NDArray[np.int64], daily: Floats, lo: str, hi: str) -> float | None:
    sel = (days >= _day(lo)) & (days < _day(hi))
    return float(np.prod(1.0 + daily[sel]) - 1.0) if sel.any() else None


def _sub(days: NDArray[np.int64], daily: Floats, lo: str, hi: str) -> dict[str, float] | None:
    sel = (days >= _day(lo)) & (days < _day(hi))
    if not sel.any():
        return None
    r = daily[sel]
    return {
        "return": float(np.prod(1.0 + r) - 1.0),
        "cagr": cagr(r),
        "sr_annual": annual_sr(r),
        "days": int(sel.sum()),
    }


def _asset_contrib(m: Market, res: Result) -> dict[str, float]:
    px = m.open
    r = np.zeros_like(px)
    ok = np.isfinite(px[1:]) & np.isfinite(px[:-1])
    r[:-1] = np.where(ok, px[1:] / np.where(ok, px[:-1], 1.0) - 1.0, 0.0)
    contrib = (res.weights * r).sum(axis=0)
    exposure = np.abs(res.weights).sum(axis=0)
    share = exposure / max(exposure.sum(), 1e-12)
    return {s: float(contrib[i]) for i, s in enumerate(m.symbols) if share[i] >= 0.01}


def _prior_returns(
    prereg: Prereg,
    ledger: Ledger,
    period: str,
    dsha: str,
    days: NDArray[np.int64],
    namer: Callable[[dict[str, Any]], str] = _trend_name,
) -> tuple[Floats | None, dict[str, Any] | None]:
    """Recorded returns of earlier hypotheses of the family, on exactly the same days."""
    if not prereg.prior_trials:
        return None, None
    entries = ledger.trials_of(prereg.prior_trials, period, dsha)
    found = {e["hypothesis"] for e in entries}
    missing = sorted(set(prereg.prior_trials) - found)
    if missing:
        raise RuntimeError(f"no recorded trials of {missing} on {period} / data {dsha}")
    cols = []
    for e in entries:
        d, r = ledger.load_returns(e)
        if not np.array_equal(d, days):
            raise RuntimeError(f"trial {e['trial_id']} of {e['hypothesis']} covers other days")
        cols.append(r)
    best = max(entries, key=lambda e: float(e["sr_annual"]))
    summary = {
        "hypotheses": sorted(found),
        "trials": len(entries),
        "best": f"{best['hypothesis']} {namer(best['params'])}",
        "best_sr_annual": float(best["sr_annual"]),
    }
    return np.column_stack(cols), summary


def run_trend(
    prereg: Prereg,
    prereg_sha: str,
    root: Path,
    repo: Path,
    *,
    final: bool = False,
    exploratory: bool = False,
) -> dict[str, Any]:
    universe = read_universe(repo / prereg.universe_file)
    costs = CostModel(prereg.costs.fee_bps, prereg.costs.slip_top2_bps, prereg.costs.slip_rest_bps)
    v = prereg.validation
    m = _market(prereg, root, universe, prereg.dev_end)
    wf = _weights(prereg)
    first_day = _day(prereg.data_start) + prereg.warmup_days
    combos = prereg.combos()
    trials: list[Trial] = []
    for c in combos:
        p = TrendParams(**c, **prereg.fixed)
        days, daily, res = _run_trial(m, p, costs, first_day, wf)
        trials.append(Trial(_trend_name(c), c, days, daily, res))
        log.info("trial_done", trial=trials[-1].name, sr=round(annual_sr(daily), 2))
    commit, dirty = git_state(repo)
    exploratory = exploratory or dirty
    dsha = data_sha(repo, prereg.data_manifests)
    period = f"{prereg.data_start}..{prereg.dev_end}"
    ledger = Ledger(repo)
    prior, prior_summary = _prior_returns(prereg, ledger, period, dsha, trials[0].days)
    R = np.column_stack([t.daily for t in trials])
    fs = family_stats(R, [t.name for t in trials], v.hold_days, v.lookback_days, prior)
    best = trials[fs.best]
    bp = TrendParams(**best.params, **prereg.fixed)
    bench_p = TrendParams(168, 24, "sign", long_only=True, **prereg.fixed)
    bdays, bench, _ = _run_trial(m, bench_p, costs, first_day)
    alpha = alpha_vs(best.daily, bench, v.hold_days)
    sens = {
        "maliyet ×1,5": annual_sr(_run_trial(m, bp, costs.scaled(1.5), first_day, wf)[1]),
        "maliyet ×2": annual_sr(_run_trial(m, bp, costs.scaled(2.0), first_day, wf)[1]),
        "1 bar gecikme": annual_sr(_run_trial(m, bp, costs, first_day, wf, exec_lag=1)[1]),
        "saatlik VWAP ile işlem": annual_sr(
            _run_trial(m, bp, costs, first_day, wf, price="vwap")[1]
        ),
    }
    contrib = _asset_contrib(m, best.result)
    yearly = _yearly(best.days, best.daily)
    bench_yearly = _yearly(bdays, bench)
    stress = {
        name: {
            "strateji": _window(best.days, best.daily, lo, hi),
            "al-tut": _window(bdays, bench, lo, hi),
        }
        for lo, hi, name in prereg.stress_windows
    }
    subperiods = {name: _sub(best.days, best.daily, lo, hi) for lo, hi, name in prereg.subperiods}
    gates: list[Gate] = [
        *fs.gates,
        plateau(fs.sr_annual, fs.best, _neighbours(combos, prereg.grid, fs.best)),
        Gate(
            "maliyet_x1_5",
            sens["maliyet ×1,5"] > 0,
            f"SR {sens['maliyet ×1,5']:.2f}",
            "maliyet ×1,5'te SR > 0",
        ),
        Gate(
            "varlik_degismezlik",
            share_positive(list(contrib.values())) >= 2 / 3,
            f"%{share_positive(list(contrib.values())) * 100:.0f} pozitif ({len(contrib)} varlık)",
            "varlıkların ≥ 2/3'ünde pozitif katkı",
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
            best.result.liquidation_flags == 0,
            str(best.result.liquidation_flags),
            "hiçbir barda likidasyon mesafesi aşılmaz",
        ),
    ]
    passed = all(g.passed for g in gates)
    base = {
        "hypothesis": prereg.id,
        "version": prereg.version,
        "prereg_sha": prereg_sha,
        "commit": commit,
        "exploratory": exploratory,
        "data_sha": dsha,
        "period": period,
    }
    for t in trials:
        ledger.record_trial(
            base,
            trial_id(prereg.id, prereg.version, t.params, dsha, period),
            t.params,
            t.days,
            t.daily,
            {"sr_annual": round(annual_sr(t.daily), 4), "days": int(t.days.size)},
        )
    res = best.result
    doc: dict[str, Any] = {
        "hypothesis": prereg.id,
        "title": prereg.title,
        "version": prereg.version,
        "prereg_sha": prereg_sha,
        "commit": commit,
        "exploratory": exploratory,
        "data_sha": dsha,
        "period": period,
        "verdict": "GECTI" if passed else "ELENDI",
        "symbols": len(m.symbols),
        "universe_top": max(rank for _, rank, _ in universe),
        "universe_label": prereg.universe_label,
        "subperiods": subperiods,
        "family": fs.as_dict(),
        "prior": prior_summary,
        "gates": [asdict(g) for g in gates],
        "best": {
            "name": best.name,
            "params": best.params,
            "sr_annual": annual_sr(best.daily),
            "cagr": cagr(best.daily),
            "max_drawdown": max_drawdown(best.daily),
            "vol_annual": float(np.std(best.daily, ddof=1) * math.sqrt(365)),
            "turnover_per_day": float(res.turnover.sum() / max(best.days.size, 1)),
            "gross_sum": float(res.gross.sum()),
            "cost_sum": float(res.cost.sum()),
            "funding_sum": float(res.funding.sum()),
            "yearly": yearly,
        },
        "benchmark": {
            "sr_annual": annual_sr(bench),
            "cagr": cagr(bench),
            "max_drawdown": max_drawdown(bench),
            "yearly": bench_yearly,
        },
        "alpha": asdict(alpha),
        "sensitivity_sr_annual": sens,
        "stress": stress,
        "asset_contribution": dict(sorted(contrib.items(), key=lambda kv: kv[1])),
        "program_trials": ledger.count(),
        "lockbox": None,
    }
    if final:
        if not passed:
            raise RuntimeError("development gates failed: the lockbox stays closed")
        ledger.open_lockbox(base | {"best": best.name})
        full = _market(prereg, root, universe, prereg.lockbox_end)
        days, daily, _ = _run_trial(full, bp, costs, first_day, wf)
        lock = days >= _day(prereg.dev_end)
        lsr = annual_sr(daily[lock])
        p5 = float(np.percentile(fs.cpcv_path_sr_annual, 5))
        doc["lockbox"] = {
            "days": int(lock.sum()),
            "sr_annual": lsr,
            "return": float(np.prod(1.0 + daily[lock]) - 1.0),
            "cpcv_p5": p5,
            "passed": lsr > 0 and lsr >= p5,
        }
        doc["verdict"] = "GECTI" if doc["lockbox"]["passed"] else "ELENDI"
    return doc


def first_trading_day(m: Market) -> str:
    return _iso(int(m.grid[0] // DAY_NS))
