"""Live inputs of the model, from Binance's public REST API, turned into features by the
**same** code that built the training data (:func:`quanta.research.features.symbol_frame`
and :func:`~quanta.research.features.assemble`).

Per universe symbol (USDT perpetual) the bot keeps:

* 1-minute klines (``/fapi/v1/klines``: OHLC, volume, quote volume, trade count, taker buy
  volume — the archive's columns), ``history_days`` back for the volatility warm-up;
* 5-minute premium index klines (``/fapi/v1/premiumIndexKlines``);
* 5-minute metrics: open interest, top-trader position ratio, account ratio, taker ratio
  (``/futures/data/*``, the archive's ``metrics`` columns), joined on their timestamp;
* funding settlements (``/fapi/v1/fundingRate``).

Timing matches the research: a decision is taken at the close of every 5-minute block, on
bars that have closed; metrics count 6 minutes after their timestamp, the premium bar when
it has closed, funding one minute after settlement.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

from quanta.core.clock import Clock
from quanta.core.log import get_logger
from quanta.research.features import (
    METRICS_DELAY_NS,
    PREMIUM_BAR_NS,
    Frame,
    Series,
    assemble,
    symbol_frame,
)
from quanta.research.minute import MIN_NS, Bars
from quanta.venues.binance_usdm.rest import BinanceUsdmRest

log = get_logger(__name__)
MS = 1_000_000
GRID_NS = 5 * MIN_NS
METRIC_PATHS = {
    "oi": ("/futures/data/openInterestHist", "sumOpenInterest"),
    "top_ls": ("/futures/data/topLongShortPositionRatio", "longShortRatio"),
    "acc_ls": ("/futures/data/globalLongShortAccountRatio", "longShortRatio"),
    "taker_ls": ("/futures/data/takerlongshortRatio", "buySellRatio"),
}
KLINE_PAGE = 1500


def kline_weight(limit: int) -> int:
    if limit < 100:
        return 1
    if limit < 500:
        return 2
    return 5 if limit <= 1000 else 10


@dataclass(slots=True)
class SymbolData:
    symbol: str
    rank: int
    # minute (open time // 1 min) → (o, h, l, c, v, qv, n, tb)
    bars: dict[int, tuple[float, ...]] = field(default_factory=dict)
    premium: dict[int, float] = field(default_factory=dict)  # open time ns → close
    metrics: dict[str, dict[int, float]] = field(default_factory=dict)  # name → ts ns → value
    funding: dict[int, float] = field(default_factory=dict)  # minute-rounded time ns → rate

    def last_minute(self) -> int | None:
        return max(self.bars) if self.bars else None

    def to_bars(self) -> Bars:
        minutes = np.array(sorted(self.bars), dtype=np.int64)
        rows = np.array([self.bars[int(m)] for m in minutes], dtype=np.float64).reshape(-1, 8)
        ft = np.array(sorted(self.funding), dtype=np.int64)
        fr = np.array([self.funding[int(t)] for t in ft], dtype=np.float64)
        return Bars(
            self.symbol,
            minutes * MIN_NS,
            rows[:, 0],
            rows[:, 1],
            rows[:, 2],
            rows[:, 3],
            rows[:, 4],
            rows[:, 5],
            rows[:, 7],
            np.full(minutes.size, self.rank, dtype=np.int16),
            ft,
            fr,
            rows[:, 6],
        )

    def premium_series(self) -> Series | None:
        if not self.premium:
            return None
        ot = np.array(sorted(self.premium), dtype=np.int64)
        return Series(ot + PREMIUM_BAR_NS, {"prem": np.array([self.premium[int(t)] for t in ot])})

    def metrics_series(self) -> Series | None:
        """Timestamps where all four metrics are known (the archive's rows hold all four)."""
        sets = [set(self.metrics.get(k, {})) for k in METRIC_PATHS]
        common = sorted(set.intersection(*sets)) if sets else []
        if not common:
            return None
        ts = np.array(common, dtype=np.int64)
        vals = {k: np.array([self.metrics[k][int(t)] for t in ts]) for k in METRIC_PATHS}
        return Series(ts + METRICS_DELAY_NS, vals)

    def trim(self, keep_from_minute: int) -> None:
        for m in [m for m in self.bars if m < keep_from_minute]:
            del self.bars[m]
        cut = keep_from_minute * MIN_NS - 2 * 86400 * 10**9
        for t in [t for t in self.premium if t < cut]:
            del self.premium[t]
        for d in self.metrics.values():
            for t in [t for t in d if t < cut]:
                del d[t]


def parse_klines(rows: list[list[Any]], now_ms: int) -> dict[int, tuple[float, ...]]:
    """REST kline rows → minute → values; the still open bar is dropped."""
    out: dict[int, tuple[float, ...]] = {}
    for r in rows:
        open_ms, close_ms = int(r[0]), int(r[6])
        if close_ms >= now_ms or open_ms % 60_000:
            continue
        out[open_ms // 60_000] = (
            float(r[1]),
            float(r[2]),
            float(r[3]),
            float(r[4]),
            float(r[5]),
            float(r[7]),
            float(r[8]),
            float(r[9]),
        )
    return out


@dataclass(slots=True)
class Snapshot:
    """Features of every universe symbol at one decision time."""

    t_ns: int
    frame: Frame
    symbols: list[str]  # frame.sym → USDT symbol

    def rows(self) -> list[tuple[str, NDArray[np.float32], float, float]]:
        """(symbol, feature row, σ of 1-minute returns, decision bar close)."""
        return [
            (self.symbols[int(self.frame.sym[k])], self.frame.x[k], float(self.frame.sigma[k]),
             float(self.frame.close[k]))
            for k in range(len(self.frame))
        ]  # fmt: skip


class LiveData:
    def __init__(
        self,
        rest: BinanceUsdmRest,
        universe: list[tuple[int, str]],
        clock: Clock,
        history_days: int = 7,
    ) -> None:
        self.rest = rest
        self.clock = clock
        self.history_days = history_days
        self.data = {s: SymbolData(s, r) for r, s in universe}
        # alphabetical, as the training frame numbers its symbols: cross-sectional ranks break
        # ties (equal funding rates are common) by that order
        self.symbols = sorted(s for _, s in universe)
        self._funding_ns = 0

    def now_ms(self) -> int:
        return self.clock.now_ns() // MS

    # -- loading ---------------------------------------------------------------------------
    async def _klines(self, symbol: str, limit: int, start_ms: int | None = None) -> list[Any]:
        params: dict[str, Any] = {"symbol": symbol, "interval": "1m", "limit": limit}
        if start_ms is not None:
            params["startTime"] = start_ms
        r = await self.rest.get("/fapi/v1/klines", params, kline_weight(limit))
        out: list[Any] = r.json()
        return out

    async def _history(self, sd: SymbolData) -> None:
        start = (self.now_ms() - self.history_days * 86_400_000) // 60_000 * 60_000
        while start < self.now_ms() - 60_000:
            rows = await self._klines(sd.symbol, KLINE_PAGE, start)
            if not rows:
                break
            sd.bars.update(parse_klines(rows, self.now_ms()))
            nxt = int(rows[-1][0]) + 60_000
            if nxt <= start or len(rows) < KLINE_PAGE:
                break
            start = nxt

    async def _premium(self, sd: SymbolData, limit: int) -> None:
        r = await self.rest.get(
            "/fapi/v1/premiumIndexKlines",
            {"symbol": sd.symbol, "interval": "5m", "limit": limit},
            kline_weight(limit),
        )
        now = self.now_ms()
        for row in r.json():
            if int(row[6]) < now:  # closed bars only
                sd.premium[int(row[0]) * MS] = float(row[4])

    async def _metrics(self, sd: SymbolData, limit: int) -> None:
        for name, (path, key) in METRIC_PATHS.items():
            r = await self.rest.get(path, {"symbol": sd.symbol, "period": "5m", "limit": limit})
            d = sd.metrics.setdefault(name, {})
            for row in r.json():
                v = row.get(key)
                if v is not None:
                    d[int(row["timestamp"]) * MS] = float(v)

    async def _funding(self, sd: SymbolData) -> None:
        r = await self.rest.get("/fapi/v1/fundingRate", {"symbol": sd.symbol, "limit": 10})
        for row in r.json():
            t = (int(row["fundingTime"]) * MS + MIN_NS // 2) // MIN_NS * MIN_NS
            sd.funding[t] = float(row["fundingRate"])

    async def load(self) -> None:
        """History for every symbol (a few requests each; run once at start)."""
        for sd in self.data.values():
            await self._history(sd)
            await self._premium(sd, 500)
            await self._metrics(sd, 500)
            await self._funding(sd)
            log.info("bot_history_loaded", symbol=sd.symbol, bars=len(sd.bars))
        self._funding_ns = self.clock.now_ns()

    async def update(self, decision_ns: int, wait_s: float = 25.0) -> list[str]:
        """Bring every symbol up to the bar closing at ``decision_ns``; returns the symbols
        whose bar is still missing after ``wait_s`` (they get no decision this time)."""
        target = decision_ns // MIN_NS - 1  # the decision bar's minute
        deadline = self.clock.now_ns() + int(wait_s * 1e9)
        pending = [s for s, sd in self.data.items() if (sd.last_minute() or 0) < target]
        while pending:
            for s in pending:
                sd = self.data[s]
                last = sd.last_minute()
                gap = target - last if last else 10**9
                if gap > 1400:  # long outage (sleep, network): reload the history
                    sd.bars.clear()
                    await self._history(sd)
                else:
                    sd.bars.update(
                        parse_klines(await self._klines(s, min(gap + 3, 1000)), self.now_ms())
                    )
            pending = [s for s, sd in self.data.items() if (sd.last_minute() or 0) < target]
            if not pending or self.clock.now_ns() > deadline:
                break
            await asyncio.sleep(1.0)
        for sd in self.data.values():
            await self._premium(sd, 15)
            await self._metrics(sd, 15)
        if self.clock.now_ns() - self._funding_ns > 1800 * 10**9:
            for sd in self.data.values():
                await self._funding(sd)
            self._funding_ns = self.clock.now_ns()
        keep = target - (self.history_days + 1) * 1440
        for sd in self.data.values():
            sd.trim(keep)
        return pending

    # -- features --------------------------------------------------------------------------
    def snapshot(self, decision_ns: int) -> Snapshot | None:
        """Features at ``decision_ns`` (a multiple of 5 minutes) for every symbol whose
        decision bar is in; None when BTC is missing (a feature for every coin)."""
        if decision_ns % GRID_NS:
            raise ValueError("decisions are taken on the 5-minute grid")
        bar_t = decision_ns - MIN_NS
        frames: list[Frame] = []
        btc: Frame | None = None
        for k, s in enumerate(self.symbols):
            sd = self.data[s]
            if bar_t // MIN_NS not in sd.bars:
                continue
            bars = sd.to_bars()
            i = int(np.searchsorted(bars.t, bar_t))
            f = symbol_frame(
                bars,
                k,
                (),
                sd.premium_series(),
                sd.metrics_series(),
                rows=np.array([i], dtype=np.int64),
            )
            frames.append(f)
            if s == "BTCUSDT":
                btc = f
        if btc is None or not frames:
            return None
        return Snapshot(decision_ns, assemble(frames, btc), list(self.symbols))
