"""Features of model M1 on a 5-minute decision grid (``research/prereg/M1.yaml``).

A decision is taken at the close of every 1-minute bar whose minute ends a 5-minute block
(``minute % 5 == 4``). Everything a row holds is known at that close:

* candle features use bars up to and including the decision bar;
* the 5-minute premium index bar is used once it has closed (open time + 5 min);
* metrics (open interest, long/short ratios) ``METRICS_DELAY_NS`` after ``create_time``;
* a funding rate once it is settled (funding time + 1 min);
* volatility and volume baselines are exponentially weighted means of the past.

Labels are the log return from the next bar's open to the open ``h`` minutes later, the
return a trade entered after the decision can earn, divided by the volatility known at the
decision (``y = r / (σ·√h)``) and clipped at ±``LABEL_CLIP``.

:func:`symbol_frame` works on one symbol; :func:`assemble` joins the symbols of the universe
and adds the cross-sectional features (market and BTC moves, ranks within the minute).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pyarrow as pa
from numpy.typing import NDArray

from quanta.research.intraday import ewma
from quanta.research.intraday_strategies import rolling_extreme
from quanta.research.minute import MIN_NS, Bars

Floats = NDArray[np.float64]
Ints = NDArray[np.int64]
F32 = NDArray[np.float32]

GRID_MIN = 5
DAY_MIN = 1440
VOL_HALFLIFE_MIN = 1440.0
WARMUP_MIN = 3 * DAY_MIN  # a symbol's first 3 days of bars only feed the baselines
LABEL_CLIP = 5.0
METRICS_DELAY_NS = 6 * MIN_NS
METRICS_STALE_NS = 30 * MIN_NS
PREMIUM_BAR_NS = 5 * MIN_NS
MIN_COINS = 5  # cross-sectional features need at least this many coins in the minute

RET_WINDOWS = (5, 15, 60, 240, 1440)
FLOW_WINDOWS = (5, 15, 60)
LOC_WINDOWS = (60, 240, 1440)
SYMBOL_FEATURES = (
    *(f"ret_{k}" for k in RET_WINDOWS),
    "rv_60",
    "rv_240",
    "range_60",
    *(f"imb_{k}" for k in FLOW_WINDOWS),
    "qimb_4",
    "volr_5",
    "volr_60",
    "cntr_5",
    "cntr_60",
    *(f"loc_hi_{k}" for k in LOC_WINDOWS),
    *(f"loc_lo_{k}" for k in LOC_WINDOWS),
    "funding_8h",
    "funding_left",
    "prem",
    "prem_chg_60",
    "oi_chg_60",
    "oi_chg_240",
    "oi_chg_1440",
    "top_ls",
    "acc_ls",
    "taker_ls",
    "top_ls_chg_60",
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    "quarter_phase",
)
MARKET_FEATURES = (
    *(f"btc_{k}" for k in (5, 15, 60)),
    *(f"mkt_{k}" for k in (5, 15, 60)),
    *(f"res_{k}" for k in (5, 15, 60, 240)),
)
CS_SOURCES = ("ret_60", "ret_240", "ret_1440", "volr_60", "imb_60", "oi_chg_240", "funding_8h")
CS_FEATURES = tuple(f"cs_{s}" for s in CS_SOURCES)
FEATURES = (*SYMBOL_FEATURES, *MARKET_FEATURES, *CS_FEATURES)
RAW_RETURNS = (5, 15, 60, 240)  # raw log returns kept for the market features


@dataclass(slots=True)
class Frame:
    """Decision rows (time order). ``x`` columns follow ``names``."""

    t: Ints  # decision time: close of the decision bar (ns)
    sym: NDArray[np.int32]
    rank: NDArray[np.int16]
    close: Floats  # close of the decision bar
    sigma: Floats  # σ of 1-minute log returns known at the decision
    x: F32
    names: tuple[str, ...]
    y: dict[int, F32] = field(default_factory=dict)  # horizon (min) → clipped label
    raw: dict[int, Floats] = field(default_factory=dict)  # window → raw log return

    def __len__(self) -> int:
        return int(self.t.size)

    def col(self, name: str) -> F32:
        out: F32 = self.x[:, self.names.index(name)]
        return out

    def take(self, idx: NDArray[np.int64]) -> Frame:
        return Frame(
            self.t[idx],
            self.sym[idx],
            self.rank[idx],
            self.close[idx],
            self.sigma[idx],
            self.x[idx],
            self.names,
            {h: v[idx] for h, v in self.y.items()},
            {k: v[idx] for k, v in self.raw.items()},
        )


@dataclass(slots=True)
class Series:
    """An auxiliary time series of one symbol: values known from ``avail`` on."""

    avail: Ints
    values: dict[str, Floats]


def premium_series(table: pa.Table | None) -> Series | None:
    """5-minute premium index klines → close, known when the bar has closed."""
    if table is None or table.num_rows == 0:
        return None
    ot = np.asarray(table.column("open_time").cast(pa.int64()).to_numpy(), dtype=np.int64)
    close = np.asarray(table.column("close").to_numpy(), dtype=np.float64)
    order = np.argsort(ot, kind="stable")
    ot, close = ot[order], close[order]
    keep = np.ones(ot.size, dtype=bool)
    keep[1:] = ot[1:] != ot[:-1]
    return Series(ot[keep] + PREMIUM_BAR_NS, {"prem": close[keep]})


def metrics_series(table: pa.Table | None) -> Series | None:
    """5-minute metrics, deduplicated (early files hold every row twice), known
    ``METRICS_DELAY_NS`` after ``create_time``."""
    if table is None or table.num_rows == 0:
        return None
    ct = np.asarray(table.column("create_time").cast(pa.int64()).to_numpy(), dtype=np.int64)
    order = np.argsort(ct, kind="stable")
    ct = ct[order]
    keep = np.ones(ct.size, dtype=bool)
    keep[1:] = ct[1:] != ct[:-1]
    cols = {
        "oi": "sum_open_interest",
        "top_ls": "sum_toptrader_long_short_ratio",
        "acc_ls": "count_long_short_ratio",
        "taker_ls": "sum_taker_long_short_vol_ratio",
    }
    vals = {
        k: np.asarray(table.column(c).to_numpy(zero_copy_only=False), dtype=np.float64)[order][keep]
        for k, c in cols.items()
    }
    return Series(ct[keep] + METRICS_DELAY_NS, vals)


def asof(avail: Ints, values: Floats, at: Ints, stale_ns: int | None = None) -> Floats:
    """The last value with ``avail <= at`` (NaN before the first, or when older than
    ``stale_ns``)."""
    j = np.searchsorted(avail, at, side="right") - 1
    out = np.where(j >= 0, values[np.maximum(j, 0)], np.nan)
    if stale_ns is not None:
        age = at - avail[np.maximum(j, 0)]
        out = np.where((j >= 0) & (age <= stale_ns), out, np.nan)
    return out


def _cum(x: Floats) -> Floats:
    out = np.zeros(x.size + 1)
    np.cumsum(np.nan_to_num(x), out=out[1:])
    return out


def _safe_log(x: Floats) -> Floats:
    with np.errstate(divide="ignore", invalid="ignore"):
        out: Floats = np.where(x > 0, np.log(np.where(x > 0, x, 1.0)), np.nan)
    return out


def decision_bars(bars: Bars) -> Ints:
    """Indices of the bars that close a 5-minute block, after the warm-up."""
    m = bars.minute
    if m.size == 0:
        return np.zeros(0, dtype=np.int64)
    ok = (m % GRID_MIN == GRID_MIN - 1) & (m >= m[0] + WARMUP_MIN)
    return np.flatnonzero(ok).astype(np.int64)


def symbol_frame(
    bars: Bars,
    sym: int,
    horizons: tuple[int, ...],
    premium: Series | None = None,
    metrics: Series | None = None,
    rows: Ints | None = None,
) -> Frame:
    """Per-symbol features and labels at the decision bars ``rows`` (default: every 5-minute
    decision after the warm-up). Undefined values (e.g. zero volatility) become NaN."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return _symbol_frame(bars, sym, horizons, premium, metrics, rows)


def _symbol_frame(
    bars: Bars,
    sym: int,
    horizons: tuple[int, ...],
    premium: Series | None,
    metrics: Series | None,
    rows: Ints | None,
) -> Frame:
    m = bars.minute
    n = len(bars)
    i = decision_bars(bars) if rows is None else rows
    t_dec = bars.t[i] + MIN_NS  # the decision bar's close
    logc = np.log(bars.c)
    r1 = np.full(n, np.nan)
    if n > 1:
        r1[1:] = np.diff(logc)
        r1[1:][np.diff(m) != 1] = np.nan  # no return across a gap
    sigma_all = np.sqrt(ewma(r1 * r1, VOL_HALFLIFE_MIN))
    sigma = sigma_all[i]
    feats: dict[str, Floats] = {}

    def back(k: int) -> tuple[Ints, NDArray[np.bool_]]:
        """Index of the bar ``k`` minutes before each decision bar (the last one at or
        before that minute), and whether it is close enough to count."""
        j = np.searchsorted(m, m[i] - k, side="right") - 1
        ok = (j >= 0) & (m[i] - m[np.maximum(j, 0)] <= k + GRID_MIN)
        return np.maximum(j, 0), ok

    raw: dict[int, Floats] = {}
    for k in RET_WINDOWS:
        j, ok = back(k)
        lr = np.where(ok, logc[i] - logc[j], np.nan)
        feats[f"ret_{k}"] = lr / (sigma * math.sqrt(k))
        if k in RAW_RETURNS:
            raw[k] = lr
    c_r2 = _cum(np.where(np.isfinite(r1), r1 * r1, 0.0))
    for k in (60, 240):
        j, ok = back(k)
        feats[f"rv_{k}"] = np.where(ok, np.sqrt((c_r2[i + 1] - c_r2[j + 1]) / k), np.nan) / sigma
    hi60, lo60 = rolling_extreme(bars.h, 60, "max")[i], rolling_extreme(bars.low, 60, "min")[i]
    feats["range_60"] = np.log(hi60 / lo60) / (sigma * math.sqrt(60))
    c_v, c_tb, c_qv = _cum(bars.v), _cum(bars.tb), _cum(bars.qv)
    for k in FLOW_WINDOWS:
        j, ok = back(k)
        v = c_v[i + 1] - c_v[j + 1]
        tb = c_tb[i + 1] - c_tb[j + 1]
        with np.errstate(divide="ignore", invalid="ignore"):
            feats[f"imb_{k}"] = np.where(ok & (v > 0), (2.0 * tb - v) / v, np.nan)
    # opening imbalance of the last four quarter-hours (first minute of each)
    q = np.flatnonzero(m % 15 == 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        qi = np.where(bars.v[q] > 0, (2.0 * bars.tb[q] - bars.v[q]) / bars.v[q], np.nan)
    c_qi, c_qn = _cum(qi), _cum(np.isfinite(qi).astype(np.float64))
    p = np.searchsorted(q, i, side="right")  # quarter starts at or before the decision bar
    lo_p = np.maximum(p - 4, 0)
    cnt = c_qn[p] - c_qn[lo_p]
    with np.errstate(divide="ignore", invalid="ignore"):
        feats["qimb_4"] = np.where(cnt >= 3, (c_qi[p] - c_qi[lo_p]) / cnt, np.nan)
    qv_mean = ewma(bars.qv, VOL_HALFLIFE_MIN)
    has_n = bars.n.size == n
    c_n = _cum(bars.n) if has_n else None
    n_mean = ewma(bars.n, VOL_HALFLIFE_MIN) if has_n else None
    for k in (5, 60):
        j, ok = back(k)
        feats[f"volr_{k}"] = np.where(
            ok, _safe_log((c_qv[i + 1] - c_qv[j + 1]) / (qv_mean[i] * k)), np.nan
        )
        if c_n is not None and n_mean is not None:
            feats[f"cntr_{k}"] = np.where(
                ok, _safe_log((c_n[i + 1] - c_n[j + 1]) / (n_mean[i] * k)), np.nan
            )
        else:
            feats[f"cntr_{k}"] = np.full(i.size, np.nan)
    for k in LOC_WINDOWS:
        hi = rolling_extreme(bars.h, k, "max")[i]
        lo = rolling_extreme(bars.low, k, "min")[i]
        scale = sigma * math.sqrt(k)
        feats[f"loc_hi_{k}"] = np.log(bars.c[i] / hi) / scale
        feats[f"loc_lo_{k}"] = np.log(bars.c[i] / lo) / scale
    # funding: the last settled rate (known one minute after the funding time)
    ft, fr = bars.funding_t, bars.funding_rate
    pf = np.searchsorted(ft + MIN_NS, t_dec, side="right") - 1
    f8 = np.full(i.size, np.nan)
    left = np.full(i.size, np.nan)
    ok = pf >= 1
    if ok.any():
        a = pf[ok]
        interval = np.clip((ft[a] - ft[a - 1]) / 3.6e12, 1.0, 8.0)  # hours between settlements
        f8[ok] = fr[a] * 8.0 / interval
        nxt = ft[a] + interval * 3.6e12
        left[ok] = np.clip((nxt - t_dec[ok]) / (interval * 3.6e12), 0.0, 1.0)
    feats["funding_8h"], feats["funding_left"] = f8 * 1e4, left
    if premium is not None:
        prem = premium.values["prem"]
        now = asof(premium.avail, prem, t_dec, 30 * MIN_NS)
        before = asof(premium.avail, prem, t_dec - 60 * MIN_NS, 30 * MIN_NS)
        feats["prem"], feats["prem_chg_60"] = now * 1e4, (now - before) * 1e4
    else:
        feats["prem"] = feats["prem_chg_60"] = np.full(i.size, np.nan)
    if metrics is not None:
        mv, av = metrics.values, metrics.avail

        def at(name: str, lag_min: int = 0) -> Floats:
            return asof(av, mv[name], t_dec - lag_min * MIN_NS, METRICS_STALE_NS)

        oi = _safe_log(at("oi"))
        for k in (60, 240, 1440):
            feats[f"oi_chg_{k}"] = oi - _safe_log(at("oi", k))
        top = _safe_log(at("top_ls"))
        feats["top_ls"], feats["top_ls_chg_60"] = top, top - _safe_log(at("top_ls", 60))
        feats["acc_ls"] = _safe_log(at("acc_ls"))
        feats["taker_ls"] = _safe_log(at("taker_ls"))
    else:
        for name in ("oi_chg_60", "oi_chg_240", "oi_chg_1440", "top_ls", "top_ls_chg_60"):
            feats[name] = np.full(i.size, np.nan)
        feats["acc_ls"] = feats["taker_ls"] = np.full(i.size, np.nan)
    minute_of_day = (t_dec // MIN_NS) % DAY_MIN
    hour = minute_of_day / 60.0
    dow = ((t_dec // (DAY_MIN * MIN_NS)) + 3) % 7  # 1970-01-01 was a Thursday → Monday = 0
    feats["hour_sin"], feats["hour_cos"] = (
        np.sin(2 * np.pi * hour / 24),
        np.cos(2 * np.pi * hour / 24),
    )
    feats["dow_sin"], feats["dow_cos"] = np.sin(2 * np.pi * dow / 7), np.cos(2 * np.pi * dow / 7)
    feats["quarter_phase"] = ((t_dec // MIN_NS) % 15) / 15.0
    x = np.empty((i.size, len(SYMBOL_FEATURES)), dtype=np.float32)
    with np.errstate(invalid="ignore"):
        for c, name in enumerate(SYMBOL_FEATURES):
            v = np.asarray(feats[name], dtype=np.float64)
            x[:, c] = np.where(np.isfinite(v), v, np.nan)
    y = {h: labels(bars, i, sigma, h) for h in horizons}
    return Frame(
        t_dec.astype(np.int64),
        np.full(i.size, sym, dtype=np.int32),
        bars.rank[i],
        bars.c[i].astype(np.float64),
        sigma,
        x,
        SYMBOL_FEATURES,
        y,
        raw,
    )


def labels(bars: Bars, i: Ints, sigma: Floats, h: int) -> F32:
    """Open of bar ``i + 1`` to the open ``h`` minutes later, over σ·√h, clipped; NaN when
    either bar is missing."""
    m = bars.minute
    a = i + 1
    ok = a < m.size
    a = np.minimum(a, m.size - 1)
    ok &= m[a] == m[i] + 1
    b = np.searchsorted(m, m[a] + h)
    ok &= b < m.size
    b = np.minimum(b, m.size - 1)
    ok &= m[b] == m[a] + h
    with np.errstate(divide="ignore", invalid="ignore"):
        y = np.log(bars.o[b] / bars.o[a]) / (sigma * math.sqrt(h))
    out: F32 = np.clip(np.where(ok & np.isfinite(y), y, np.nan), -LABEL_CLIP, LABEL_CLIP).astype(
        np.float32
    )
    return out


def _groups(t: Ints) -> tuple[Ints, Ints]:
    """Start offsets and sizes of equal-``t`` runs (``t`` sorted)."""
    starts = np.flatnonzero(np.r_[True, t[1:] != t[:-1]])
    sizes = np.diff(np.r_[starts, t.size])
    return starts, sizes


def _group_mean(t: Ints, v: Floats) -> tuple[Floats, Floats]:
    """Per row: mean of the finite ``v`` in its time group, and their count."""
    starts, sizes = _groups(t)
    fin = np.isfinite(v)
    s = np.add.reduceat(np.where(fin, v, 0.0), starts) if t.size else np.zeros(0)
    c = np.add.reduceat(fin.astype(np.float64), starts) if t.size else np.zeros(0)
    gid = np.repeat(np.arange(starts.size), sizes)
    with np.errstate(divide="ignore", invalid="ignore"):
        mean = s / c
    return mean[gid], c[gid]


def _group_rank(t: Ints, v: Floats) -> Floats:
    """Per row: rank of ``v`` within its time group scaled to [0, 1] (NaN for missing values
    or groups with fewer than ``MIN_COINS`` values)."""
    fin = np.isfinite(v)
    order = np.lexsort((np.where(fin, v, np.inf), t))
    ts = t[order]
    starts, sizes = _groups(ts)
    gid = np.repeat(np.arange(starts.size), sizes)
    pos = np.arange(t.size) - starts[gid]
    cnt = np.add.reduceat(fin[order].astype(np.int64), starts) if t.size else np.zeros(0, int)
    n = cnt[gid]
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(fin[order] & (n >= MIN_COINS), pos / np.maximum(n - 1, 1), np.nan)
    out = np.empty(t.size)
    out[order] = r
    return out


def assemble(frames: list[Frame], btc: Frame | None) -> Frame:
    """Stack the symbols' rows in time order and add the market and cross-sectional
    features. ``btc``: BTC's own frame (its moves are a feature for every coin)."""
    parts = [f for f in frames if len(f)]
    if not parts:
        raise ValueError("no rows")
    t = np.concatenate([f.t for f in parts])
    order = np.lexsort((np.concatenate([f.sym for f in parts]), t))
    t = t[order]
    x_sym = np.concatenate([f.x for f in parts])[order]
    sigma = np.concatenate([f.sigma for f in parts])[order]
    raw = {k: np.concatenate([f.raw[k] for f in parts])[order] for k in RAW_RETURNS}
    extra: dict[str, Floats] = {}
    for k in (5, 15, 60):
        if btc is not None and len(btc):
            b = asof(btc.t, btc.col(f"ret_{k}").astype(np.float64), t)
            same = np.isin(t, btc.t)
            extra[f"btc_{k}"] = np.where(same, b, np.nan)
        else:
            extra[f"btc_{k}"] = np.full(t.size, np.nan)
    for k in RAW_RETURNS:
        mean, cnt = _group_mean(t, raw[k])
        scale = sigma * math.sqrt(k)
        if k in (5, 15, 60):
            extra[f"mkt_{k}"] = np.where(cnt >= MIN_COINS, mean / scale, np.nan)
        extra[f"res_{k}"] = np.where(cnt >= MIN_COINS, (raw[k] - mean) / scale, np.nan)
    names = SYMBOL_FEATURES
    for s in CS_SOURCES:
        extra[f"cs_{s}"] = _group_rank(t, x_sym[:, names.index(s)].astype(np.float64))
    x = np.empty((t.size, len(FEATURES)), dtype=np.float32)
    x[:, : len(SYMBOL_FEATURES)] = x_sym
    for c, name in enumerate(FEATURES[len(SYMBOL_FEATURES) :], start=len(SYMBOL_FEATURES)):
        x[:, c] = extra[name]
    horizons = parts[0].y.keys()
    return Frame(
        t,
        np.concatenate([f.sym for f in parts])[order],
        np.concatenate([f.rank for f in parts])[order],
        np.concatenate([f.close for f in parts])[order],
        sigma,
        x,
        FEATURES,
        {h: np.concatenate([f.y[h] for f in parts])[order] for h in horizons},
        raw,
    )
