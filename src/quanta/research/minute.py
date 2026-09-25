"""One symbol's 1-minute bars for intraday research, with its point-in-time universe rank.

Layout (written by ``quanta research fetch --minute``)::

    <root>/lake/binance_vision_um/klines_1m/symbol=S/month=YYYY-MM/part-0.parquet

Timing: bar ``i`` covers [t[i], t[i] + 1 min) and is known at its close. A decision taken on
bar ``i`` can trade at the earliest in bar ``i + 1`` (the engine enforces it). Gaps (missing
minutes) are kept as gaps: ``t`` is strictly increasing but not necessarily contiguous.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from numpy.typing import NDArray

MIN_NS = 60 * 10**9
Floats = NDArray[np.float64]
Ints = NDArray[np.int64]


@dataclass(slots=True)
class Bars:
    symbol: str
    t: Ints  # bar open time, ns (UTC), strictly increasing, minute aligned
    o: Floats
    h: Floats
    low: Floats
    c: Floats
    v: Floats  # base volume
    qv: Floats  # quote volume
    tb: Floats  # taker (aggressive) buy base volume
    rank: NDArray[np.int16]  # universe rank of the bar's month; 0 = not in the universe
    funding_t: Ints  # funding times (ns, minute), sorted
    funding_rate: Floats

    def __len__(self) -> int:
        return int(self.t.size)

    @property
    def minute(self) -> Ints:
        """Minute index (t / 1 min), handy for gap checks."""
        return self.t // MIN_NS


def _ts(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp()) * 10**9


def _files(root: Path, name: str, symbol: str) -> list[Path]:
    base = root / "lake" / "binance_vision_um" / name / f"symbol={symbol}"
    return sorted(base.glob("*/part-0.parquet"))


def _ns(col: pa.ChunkedArray) -> Ints:
    return np.asarray(col.cast(pa.int64()).to_numpy(zero_copy_only=False), dtype=np.int64)


def _f(col: pa.ChunkedArray) -> Floats:
    return np.asarray(col.to_numpy(zero_copy_only=False), dtype=np.float64)


def month_of(t: Ints) -> NDArray[np.str_]:
    days, inv = np.unique(t // (86400 * 10**9), return_inverse=True)
    names = np.array([datetime.fromtimestamp(int(d) * 86400, UTC).strftime("%Y-%m") for d in days])
    return names[inv]


def load_bars(
    root: Path, universe: list[tuple[str, int, str]], symbol: str, start: str, end: str
) -> Bars | None:
    """Bars of ``symbol`` in [start, end) (YYYY-MM-DD), or None when there are none."""
    lo, hi = _ts(start), _ts(end)
    cols = ["open_time", "open", "high", "low", "close", "volume", "quote_volume"]
    cols.append("taker_buy_volume")
    tables = []
    for f in _files(root, "klines_1m", symbol):
        month = f.parent.name.split("=", 1)[1]
        if month + "-31" < start[:7] + "-00" or month + "-01" >= end:
            continue
        tables.append(pq.read_table(f, columns=cols))
    if not tables:
        return None
    k = pa.concat_tables(tables)
    t = _ns(k.column("open_time"))
    keep = (t >= lo) & (t < hi) & (t % MIN_NS == 0)
    order = np.argsort(t[keep], kind="stable")
    t = t[keep][order]
    first = np.ones(t.size, dtype=bool)
    first[1:] = t[1:] != t[:-1]  # duplicate minutes (overlapping files): keep the first

    def col(name: str) -> Floats:
        out: Floats = _f(k.column(name))[keep][order][first]
        return out

    t = t[first]
    if t.size == 0:
        return None
    ranks = {(m, s): r for m, r, s in universe if s == symbol}
    months = month_of(t)
    rank = np.zeros(t.size, dtype=np.int16)
    for m in np.unique(months):
        rank[months == m] = ranks.get((str(m), symbol), 0)
    ft, fr = _funding(root, symbol, lo, hi)
    return Bars(
        symbol,
        t,
        col("open"),
        col("high"),
        col("low"),
        col("close"),
        col("volume"),
        col("quote_volume"),
        col("taker_buy_volume"),
        rank,
        ft,
        fr,
    )


def _funding(root: Path, symbol: str, lo: int, hi: int) -> tuple[Ints, Floats]:
    files = _files(root, "fundingRate", symbol)
    if not files:
        return np.zeros(0, dtype=np.int64), np.zeros(0)
    fr = pa.concat_tables(
        [pq.read_table(f, columns=["calc_time", "last_funding_rate"]) for f in files]
    )
    ft = (_ns(fr.column("calc_time")) + MIN_NS // 2) // MIN_NS * MIN_NS
    rate = _f(fr.column("last_funding_rate"))
    keep = (ft >= lo) & (ft < hi)
    order = np.argsort(ft[keep], kind="stable")
    return ft[keep][order], rate[keep][order]
