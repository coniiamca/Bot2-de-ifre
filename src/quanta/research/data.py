"""Build a point-in-time :class:`Market` from the research Parquet files.

Layout (written by :mod:`quanta.research.fetch`)::

    <root>/lake/binance_vision_um/klines_1h/symbol=S/month=YYYY-MM/part-0.parquet
    <root>/lake/binance_vision_um/premiumIndexKlines_1h/symbol=S/…
    <root>/lake/binance_vision_um/fundingRate/symbol=S/month=…
    <root>/lake/binance_vision_um/metrics/symbol=S/date=YYYY-MM-DD/…

Rules (see :mod:`quanta.research.panel`): klines and premium are available one hour after
``open_time``; a funding rate at its funding time (calc_time rounded to the minute) + 1 min,
and it is charged at the grid hour of that time; metrics 10 minutes after ``create_time`` and
ignored when more than 30 minutes old.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from quanta.research.fetch import symbol_windows
from quanta.research.panel import DAY_NS, HOUR_NS, Market, asof_align, kline_close_feature

MIN_NS = 60 * 10**9
METRICS_DELAY_NS = 10 * MIN_NS
METRICS_STALE_NS = 30 * MIN_NS


def _ts(ms_or_date: str) -> int:
    return int(datetime.fromisoformat(ms_or_date).replace(tzinfo=UTC).timestamp()) * 10**9


def _read(
    root: Path,
    name: str,
    symbol: str,
    columns: list[str],
    window: tuple[str, str] | None = None,
) -> pa.Table | None:
    """All partitions of ``symbol``, or only months within ``window`` (YYYY-MM, inclusive):
    the files the dataset fetch downloaded for this universe, so that other data in the lake
    (e.g. a wider universe fetched later) cannot change a result."""
    base = root / "lake" / "binance_vision_um" / name / f"symbol={symbol}"
    files = sorted(base.glob("*/part-0.parquet"))
    if window is not None:
        lo, hi = window
        files = [f for f in files if lo <= f.parent.name.split("=", 1)[1][:7] <= hi]
    if not files:
        return None
    tables = [pq.read_table(f, columns=columns) for f in files]
    return pa.concat_tables(tables)


def _ns(col: pa.ChunkedArray) -> np.ndarray:
    return np.asarray(col.cast(pa.int64()).to_numpy(zero_copy_only=False), dtype=np.int64)


def _f(col: pa.ChunkedArray) -> np.ndarray:
    return np.asarray(col.to_numpy(zero_copy_only=False), dtype=np.float64)


def load_market(
    root: Path,
    universe: list[tuple[str, int, str]],
    start: str,
    end: str,
    metrics: bool = True,
    windows: dict[str, tuple[str, str]] | None = None,
) -> Market:
    """Hourly panel from ``start`` (inclusive, YYYY-MM-DD) to ``end`` (exclusive)."""
    g0, g1 = _ts(start), _ts(end)
    grid = np.arange(g0, g1, HOUR_NS, dtype=np.int64)
    symbols = sorted({s for _, _, s in universe})
    if windows is None:
        windows = symbol_windows(universe)  # the same months fetch_dataset downloads
    t_len, n = grid.size, len(symbols)
    col = {s: i for i, s in enumerate(symbols)}

    def nan() -> np.ndarray:
        return np.full((t_len, n), np.nan)

    open_, high, low, vwap, close_ = nan(), nan(), nan(), nan(), nan()
    tradable = np.zeros((t_len, n), dtype=bool)
    funding = np.zeros((t_len, n))
    feats: dict[str, np.ndarray] = {
        k: nan() for k in ("close", "premium", "funding_8h", "oi", "top_ratio")
    }
    avail: dict[str, np.ndarray] = {k: np.full((t_len, n), -1, dtype=np.int64) for k in feats}
    for sym, j in col.items():
        k = _read(
            root,
            "klines_1h",
            sym,
            ["open_time", "open", "high", "low", "close", "volume", "quote_volume", "count"],
            windows.get(sym),
        )
        if k is not None:
            ot = _ns(k.column("open_time"))
            idx = (ot - g0) // HOUR_NS
            ok = (idx >= 0) & (idx < t_len) & (ot % HOUR_NS == 0)
            i = idx[ok]
            open_[i, j] = _f(k.column("open"))[ok]
            high[i, j] = _f(k.column("high"))[ok]
            low[i, j] = _f(k.column("low"))[ok]
            close_[i, j] = _f(k.column("close"))[ok]
            vol = _f(k.column("volume"))[ok]
            qv = _f(k.column("quote_volume"))[ok]
            vwap[i, j] = np.where(vol > 0, qv / np.where(vol > 0, vol, 1.0), open_[i, j])
            tradable[i, j] = _f(k.column("count"))[ok] > 0
            v, a = kline_close_feature(grid, ot, _f(k.column("close")))
            feats["close"][:, j], avail["close"][:, j] = v, a
        p = _read(root, "premiumIndexKlines_1h", sym, ["open_time", "close"], windows.get(sym))
        if p is not None:
            ot = _ns(p.column("open_time"))
            v, a = asof_align(ot, ot + HOUR_NS, _f(p.column("close")), grid)
            feats["premium"][:, j], avail["premium"][:, j] = v, a
        fr = _read(
            root,
            "fundingRate",
            sym,
            ["calc_time", "funding_interval_hours", "last_funding_rate"],
            windows.get(sym),
        )
        if fr is not None:
            ft = (_ns(fr.column("calc_time")) + MIN_NS // 2) // MIN_NS * MIN_NS
            rate = _f(fr.column("last_funding_rate"))
            interval = _f(fr.column("funding_interval_hours"))
            # charged at the first grid hour at or after the funding time
            idx = -((g0 - ft) // HOUR_NS)
            ok = (idx >= 0) & (idx < t_len)
            np.add.at(funding[:, j], idx[ok], rate[ok])
            per8 = rate * 8.0 / np.where(interval > 0, interval, 8.0)
            v, a = asof_align(ft, ft + MIN_NS, per8, grid)
            feats["funding_8h"][:, j], avail["funding_8h"][:, j] = v, a
        if metrics:
            mt = _read(
                root,
                "metrics",
                sym,
                ["create_time", "sum_open_interest", "sum_toptrader_long_short_ratio"],
            )
            if mt is not None:
                ct = _ns(mt.column("create_time"))
                for name, src in (
                    ("oi", "sum_open_interest"),
                    ("top_ratio", "sum_toptrader_long_short_ratio"),
                ):
                    v, a = asof_align(
                        ct, ct + METRICS_DELAY_NS, _f(mt.column(src)), grid, METRICS_STALE_NS
                    )
                    feats[name][:, j], avail[name][:, j] = v, a
    universe_mask = np.zeros((t_len, n), dtype=bool)
    rank = np.full((t_len, n), np.nan)
    days, inv = np.unique(grid // DAY_NS, return_inverse=True)
    month_of = np.array(
        [datetime.fromtimestamp(int(d) * 86400, UTC).strftime("%Y-%m") for d in days]
    )[inv]
    for month, r, sym in universe:
        rows = month_of == month
        universe_mask[rows, col[sym]] = True
        rank[rows, col[sym]] = r
    tradable &= np.isfinite(open_)
    m = Market(grid, symbols, open_, high, low, vwap, tradable, universe_mask, rank, funding)
    for name in feats:
        m.add_feature(name, feats[name], avail[name])
    return m
