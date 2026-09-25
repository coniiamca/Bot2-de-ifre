"""H2 — leverage crowding → tail asymmetry (a predictive test, not yet a strategy).

Mechanism: when many traders are leveraged on the same side (futures trade above the index,
open interest rising, top traders tilted long), a small adverse move forces liquidations
that push price further — the next day's sharp *reverse* move becomes more likely.

At every funding time (00/08/16 UTC) and for each core symbol:

* crowding score = mean of point-in-time percentile ranks, over the trailing 90 days of
  8-hour samples, of: the premium index (average of the last 8 hourly closes), the 24 h
  change in open interest (contracts), and the top traders' long/short position ratio;
* crowded long ⇔ score ≥ 0.9 (a point-in-time percentile, never a full-sample decile);
* outcome: the next 24 h return (from the hour's open) below −2σ, with σ the trailing EWMA
  volatility scaled to 24 h.

Crowding follows rallies and calm markets, so the comparison is made within strata of the
trailing 7-day return and volatility (terciles, point-in-time), and weighted by the number
of crowded samples. Uncertainty comes from a stationary block bootstrap of time (both
symbols resampled together). Verdicts: GEÇTİ (the difference is positive with 95 %
confidence and holds per symbol and in most years, and a 30-day-shifted placebo shows
nothing), ELENDİ (the confidence interval excludes effects of ``min_effect`` or more) or
SONUÇSUZ (neither: not enough data to tell).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from quanta.research.data import load_market
from quanta.research.ledger import Ledger, trial_id
from quanta.research.panel import HOUR_NS, Market
from quanta.research.prereg import Prereg, git_state
from quanta.research.resample import block_length, percentile_ci, stationary_bootstrap_idx
from quanta.research.runner import data_sha
from quanta.research.strategies import ewma_vol

Floats = NDArray[np.float64]
Ints = NDArray[np.int64]


@dataclass(frozen=True, slots=True)
class CrowdingParams:
    threshold: float = 0.9
    horizon_h: int = 24
    k_sigma: float = 2.0
    window_days: int = 90
    min_samples: int = 60
    vol_halflife_h: int = 168
    placebo_shift_days: int = 30
    min_effect: float = 0.03  # smallest tail-probability increase worth trading
    n_boot: int = 2000
    seed: int = 0


def pit_percentile(x: Floats, window: int, min_obs: int) -> Floats:
    """Percentile rank of each value among itself and the previous ``window - 1`` finite
    values (ties count half); NaN until ``min_obs`` values are available."""
    out = np.full(x.size, np.nan)
    for i in range(x.size):
        if not np.isfinite(x[i]):
            continue
        w = x[max(0, i - window + 1) : i + 1]
        w = w[np.isfinite(w)]
        if w.size < min_obs:
            continue
        out[i] = (np.sum(w < x[i]) + 0.5 * (np.sum(w == x[i]) - 1)) / max(w.size - 1, 1)
    return out


@dataclass(slots=True)
class Events:
    time: Ints  # grid index of each sample
    asset: Ints
    score: Floats
    tail: NDArray[np.bool_]  # next-horizon return < −k σ
    past_tail: NDArray[np.bool_]  # previous-horizon return < −k σ (pre-trend check)
    stratum: Ints
    year: Ints


def events(m: Market, p: CrowdingParams) -> Events:
    t_len, n = m.shape
    hours = (m.grid // HOUR_NS) % 24
    samples = np.flatnonzero(hours % 8 == 0)
    close = m.features["close"]
    logc = np.log(close)
    logret = np.vstack([np.full((1, n), np.nan), np.diff(logc, axis=0)])
    vol_h = ewma_vol(logret, p.vol_halflife_h)
    prem = m.features["premium"]
    per_day = 3
    win, min_obs = p.window_days * per_day, p.min_samples
    h = p.horizon_h
    rows: dict[str, list[Any]] = {k: [] for k in ("t", "a", "s", "tail", "past", "st", "y")}
    for j in range(n):
        idx = samples
        prem8 = np.array(
            [np.nanmean(prem[max(0, i - 8) : i, j]) if i >= 8 else np.nan for i in idx]
        )
        oi = m.features["oi"][:, j]
        oi_prev = np.where(idx >= 24, oi[np.maximum(idx - 24, 0)], np.nan)
        ok_oi = (oi[idx] > 0) & (oi_prev > 0)  # a zero open interest is a data error
        doi = np.where(
            ok_oi, np.log(np.where(ok_oi, oi[idx], 1.0) / np.where(ok_oi, oi_prev, 1.0)), np.nan
        )
        ratio = m.features["top_ratio"][idx, j]
        comps = [pit_percentile(c, win, min_obs) for c in (prem8, doi, ratio)]
        score = np.mean(np.vstack(comps), axis=0)  # NaN unless all three exist
        sig = vol_h[idx, j] * math.sqrt(h)
        px = m.open[:, j]
        fwd = np.where(
            idx + h < t_len, np.log(px[np.minimum(idx + h, t_len - 1)] / px[idx]), np.nan
        )
        past = np.where(idx >= h, np.log(px[idx] / px[np.maximum(idx - h, 0)]), np.nan)
        ret7 = np.where(idx >= 168, logc[idx, j] - logc[np.maximum(idx - 168, 0), j], np.nan)
        r7_rank = pit_percentile(ret7, win, min_obs)
        v_rank = pit_percentile(sig, win, min_obs)
        ok = (
            np.isfinite(score) & np.isfinite(fwd) & np.isfinite(sig) & (sig > 0)
            & np.isfinite(past) & np.isfinite(r7_rank) & np.isfinite(v_rank)
            & m.tradable[idx, j]
        )  # fmt: skip
        years = np.array([datetime.fromtimestamp(g // 10**9, UTC).year for g in m.grid[idx]])
        rows["t"].append(idx[ok])
        rows["a"].append(np.full(ok.sum(), j))
        rows["s"].append(score[ok])
        rows["tail"].append(fwd[ok] < -p.k_sigma * sig[ok])
        rows["past"].append(past[ok] < -p.k_sigma * sig[ok])
        rows["st"].append(_tercile(r7_rank[ok]) * 3 + _tercile(v_rank[ok]))
        rows["y"].append(years[ok])
    cat = {k: np.concatenate(v) if v else np.zeros(0) for k, v in rows.items()}
    return Events(
        cat["t"].astype(np.int64),
        cat["a"].astype(np.int64),
        cat["s"].astype(np.float64),
        cat["tail"].astype(bool),
        cat["past"].astype(bool),
        cat["st"].astype(np.int64),
        cat["y"].astype(np.int64),
    )


def _tercile(r: Floats) -> Ints:
    return np.minimum((r * 3).astype(np.int64), 2)


def stratified_diff(y: NDArray[np.bool_], crowded: NDArray[np.bool_], strata: Ints) -> float:
    """Σ_s n_crowded,s · (p_crowded,s − p_rest,s) / Σ_s n_crowded,s over strata with both."""
    num = den = 0.0
    for s in np.unique(strata):
        sel = strata == s
        c, r = sel & crowded, sel & ~crowded
        nc, nr = int(c.sum()), int(r.sum())
        if nc == 0 or nr == 0:
            continue
        num += nc * (float(y[c].mean()) - float(y[r].mean()))
        den += nc
    return num / den if den else float("nan")


@dataclass(slots=True)
class TestResult:
    n_samples: int
    n_crowded: int
    base_rate: float
    crowded_rate: float
    diff: float
    ci: tuple[float, float]
    block: float


def _test(
    ev: Events, sel: NDArray[np.bool_], p: CrowdingParams, y: NDArray[np.bool_] | None = None
) -> TestResult:
    yy = ev.tail if y is None else y
    crowded = ev.score >= p.threshold
    d = stratified_diff(yy[sel], crowded[sel], ev.stratum[sel])
    times = np.unique(ev.time[sel])
    pos = {t: i for i, t in enumerate(times)}
    t_of = np.array([pos[t] for t in ev.time[sel]], dtype=np.int64)
    by_time = np.zeros(times.size)
    for i, v in zip(t_of, yy[sel], strict=True):
        by_time[i] += v
    b = max(block_length(by_time), p.horizon_h / 8 * 3)
    rng = np.random.default_rng(p.seed)
    boot = np.empty(p.n_boot)
    # events of each sample time (one per symbol): resample times, keep symbols together
    width = int(np.bincount(t_of).max()) if t_of.size else 1
    slots = np.full((times.size, width), -1, dtype=np.int64)
    fill = np.zeros(times.size, dtype=np.int64)
    for e, i in enumerate(t_of):
        slots[i, fill[i]] = e
        fill[i] += 1
    ys, cs, ss = yy[sel], crowded[sel], ev.stratum[sel]
    for k, idx in enumerate(stationary_bootstrap_idx(times.size, b, p.n_boot, rng)):
        take = slots[idx].ravel()
        take = take[take >= 0]
        boot[k] = stratified_diff(ys[take], cs[take], ss[take])
    boot = boot[np.isfinite(boot)]
    return TestResult(
        int(sel.sum()),
        int((crowded & sel).sum()),
        float(yy[sel & ~crowded].mean()) if (sel & ~crowded).any() else float("nan"),
        float(yy[sel & crowded].mean()) if (sel & crowded).any() else float("nan"),
        d,
        percentile_ci(boot) if boot.size else (float("nan"), float("nan")),
        b,
    )


def verdict(
    main: TestResult,
    per_asset: list[TestResult],
    per_year: dict[int, TestResult],
    placebo: TestResult,
    p: CrowdingParams,
) -> str:
    lo, hi = main.ci
    if lo > 0:
        consistent = all(r.diff > 0 for r in per_asset) and (
            np.mean([r.diff > 0 for r in per_year.values()]) > 0.5
        )
        placebo_quiet = placebo.ci[0] <= 0 <= placebo.ci[1]
        return "GECTI" if consistent and placebo_quiet else "SONUCSUZ"
    if hi < p.min_effect:
        return "ELENDI"
    return "SONUCSUZ"


def run_crowding(
    prereg: Prereg,
    prereg_sha: str,
    root: Path,
    repo: Path,
    *,
    final: bool = False,
    exploratory: bool = False,
) -> dict[str, Any]:
    p = CrowdingParams(**{k: v[0] for k, v in prereg.grid.items()}, **prereg.fixed)
    months = _months(prereg.data_start, prereg.lockbox_end)
    universe = [(mo, i + 1, s) for mo in months for i, s in enumerate(prereg.symbols)]
    end = prereg.lockbox_end if final else prereg.dev_end
    m = load_market(root, universe, prereg.data_start, end, metrics=True)
    m.check_point_in_time()
    ev = events(m, p)
    dev_cut = int(np.searchsorted(m.grid, _ns(prereg.dev_end)))
    dev = ev.time < dev_cut
    main = _test(ev, dev, p)
    per_asset = [_test(ev, dev & (ev.asset == j), p) for j in range(len(m.symbols))]
    per_year = {int(y): _test(ev, dev & (ev.year == y), p) for y in np.unique(ev.year[dev])}
    placebo_score = _shift_scores(ev, p.placebo_shift_days * 3)
    placebo = _test(
        Events(ev.time, ev.asset, placebo_score, ev.tail, ev.past_tail, ev.stratum, ev.year), dev, p
    )
    pre = _test(ev, dev, p, y=ev.past_tail)
    v = verdict(main, per_asset, per_year, placebo, p)
    commit, dirty = git_state(repo)
    dsha = data_sha(repo)
    period = f"{prereg.data_start}..{prereg.dev_end}"
    base = {
        "hypothesis": prereg.id, "version": prereg.version, "prereg_sha": prereg_sha,
        "commit": commit, "exploratory": exploratory or dirty, "data_sha": dsha, "period": period,
    }  # fmt: skip
    days = np.array(
        sorted({int(m.grid[t] // (86400 * 10**9)) for t in ev.time[dev]}), dtype=np.int64
    )
    Ledger(repo).record_trial(
        base,
        trial_id(prereg.id, prereg.version, asdict(p), dsha, period),
        asdict(p),
        days,
        np.zeros(days.size),
        {"diff": main.diff, "ci_lo": main.ci[0], "ci_hi": main.ci[1]},
    )
    doc: dict[str, Any] = {
        "hypothesis": prereg.id,
        "title": prereg.title,
        "version": prereg.version,
        "prereg_sha": prereg_sha,
        "commit": commit,
        "exploratory": exploratory or dirty,
        "data_sha": dsha,
        "period": period,
        "symbols": m.symbols,
        "params": asdict(p),
        "verdict": v,
        "main": asdict(main),
        "per_asset": {s: asdict(r) for s, r in zip(m.symbols, per_asset, strict=True)},
        "per_year": {str(y): asdict(r) for y, r in per_year.items()},
        "placebo": asdict(placebo),
        "pre_trend": asdict(pre),
        "lockbox": None,
    }
    if final:
        if v != "GECTI":
            raise RuntimeError("development test did not pass: the lockbox stays closed")
        Ledger(repo).open_lockbox(base)
        lock = _test(ev, ~dev, p)
        doc["lockbox"] = asdict(lock) | {"passed": lock.diff > 0}
        doc["verdict"] = "GECTI" if lock.diff > 0 else "ELENDI"
    return doc


def _shift_scores(ev: Events, shift: int) -> Floats:
    """Placebo: each symbol's crowding scores moved ``shift`` samples later in time."""
    out = np.full(ev.score.size, np.nan)
    for j in np.unique(ev.asset):
        sel = np.flatnonzero(ev.asset == j)
        s = ev.score[sel]
        out[sel[shift:]] = s[:-shift] if shift else s
    return np.where(np.isfinite(out), out, 0.0)


def _ns(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp()) * 10**9


def _months(start: str, end: str) -> list[str]:
    from quanta.research.universe import month_range

    last = datetime.fromisoformat(end).replace(tzinfo=UTC)
    return month_range(start[:7], last.strftime("%Y-%m"))
