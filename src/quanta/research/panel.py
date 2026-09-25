"""Point-in-time market panel on an hourly UTC grid.

Decision time is the hour boundary ``grid[t]``; orders execute at ``open[t]``. A signal may
only use values whose ``available_at <= grid[t]`` — :func:`asof_align` is the single place
that maps event data onto the grid, and every aligned field keeps the availability time of
the value it used (``Market.avail``) so tests can prove nothing leaks from the future.

Availability rules (Binance archive, see docs/research/02-ilk-hipotezler.md):

* 1h kline: available at open_time + 1h (the bar has closed).
* premium index 1h kline: open_time + 1h.
* funding: funding time (calc_time rounded to the minute) + 1 min.
* metrics (5-min OI and ratios): create_time + 10 min (conservative).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

Floats = NDArray[np.float64]
Ints = NDArray[np.int64]
Bools = NDArray[np.bool_]
HOUR_NS = 3600 * 10**9
DAY_NS = 24 * HOUR_NS


def asof_align(
    event_ts: Ints,
    avail_ts: Ints,
    values: Floats,
    grid: Ints,
    max_stale_ns: int | None = None,
) -> tuple[Floats, Ints]:
    """For each grid time, the latest value already available (``avail_ts <= grid``).
    Values whose event is older than ``max_stale_ns`` count as missing (NaN). Returns the
    aligned values and the availability time of each (−1 when missing)."""
    order = np.argsort(avail_ts, kind="stable")
    av = np.asarray(avail_ts)[order]
    ev = np.asarray(event_ts)[order]
    val = np.asarray(values, dtype=np.float64)[order]
    idx = np.searchsorted(av, grid, side="right") - 1
    ok = idx >= 0
    safe = np.where(ok, idx, 0)
    out = np.where(ok, val[safe], np.nan)
    src = np.where(ok, av[safe], -1)
    if max_stale_ns is not None:
        stale = ok & (grid - ev[safe] > max_stale_ns)
        out = np.where(stale, np.nan, out)
        src = np.where(stale, -1, src)
    return out, src.astype(np.int64)


@dataclass(slots=True)
class Market:
    """Hourly panel (T × N). ``open``/``high``/``low``/``vwap`` describe the bar that starts
    at ``grid[t]`` and are execution-side data (never signal inputs); ``features`` hold
    point-in-time signal inputs with their availability times in ``avail``."""

    grid: Ints
    symbols: list[str]
    open: Floats
    high: Floats
    low: Floats
    vwap: Floats
    tradable: Bools  # bar exists with trades → an order at open[t] can fill
    universe: Bools  # member of the point-in-time universe at grid[t]
    rank: Floats  # universe rank (1 = most liquid) at grid[t], NaN outside
    funding: Floats  # rate charged at grid[t] (0 elsewhere); longs pay positive rates
    features: dict[str, Floats] = field(default_factory=dict)
    avail: dict[str, Ints] = field(default_factory=dict)

    @property
    def shape(self) -> tuple[int, int]:
        return int(self.grid.size), len(self.symbols)

    def add_feature(self, name: str, values: Floats, avail: Ints) -> None:
        if values.shape != self.shape or avail.shape != self.shape:
            raise ValueError(f"{name}: shape {values.shape} != {self.shape}")
        self.features[name] = values
        self.avail[name] = avail

    def check_point_in_time(self) -> None:
        """Raise if any feature value was not yet available at its grid time."""
        for name, av in self.avail.items():
            bad = (av > self.grid[:, None]) & np.isfinite(self.features[name])
            if bad.any():
                t, n = np.argwhere(bad)[0]
                raise AssertionError(
                    f"look-ahead in {name!r}: {self.symbols[n]} at grid {self.grid[t]} "
                    f"uses a value available at {av[t, n]}"
                )

    def slice_time(self, stop: int) -> Market:
        """The first ``stop`` hours (used to keep the lockbox out of development runs)."""
        return Market(
            self.grid[:stop],
            self.symbols,
            self.open[:stop],
            self.high[:stop],
            self.low[:stop],
            self.vwap[:stop],
            self.tradable[:stop],
            self.universe[:stop],
            self.rank[:stop],
            self.funding[:stop],
            {k: v[:stop] for k, v in self.features.items()},
            {k: v[:stop] for k, v in self.avail.items()},
        )


def kline_close_feature(grid: Ints, open_time: Ints, close: Floats) -> tuple[Floats, Ints]:
    """Last closed 1h bar's close for every grid time (one symbol)."""
    return asof_align(open_time, open_time + HOUR_NS, close, grid)


def day_index(grid: Ints) -> Ints:
    return (grid // DAY_NS).astype(np.int64)
