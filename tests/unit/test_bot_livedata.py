"""Live inputs → features: the same numbers as the research pipeline for the same data, REST
parsing (the open bar is dropped), loading and updating through a fake REST client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

np = pytest.importorskip("numpy")

from quanta.bot.livedata import LiveData, SymbolData, parse_klines  # noqa: E402
from quanta.core.clock import SimClock  # noqa: E402
from quanta.research.features import (  # noqa: E402
    METRICS_DELAY_NS,
    PREMIUM_BAR_NS,
    Series,
    assemble,
    symbol_frame,
)
from quanta.research.minute import MIN_NS, Bars  # noqa: E402
from quanta.research.synthetic import make_minute_bars  # noqa: E402

SYMS = ["ADAUSDT", "BTCUSDT", "DOGEUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]


def synthetic(days: int = 6) -> dict[str, Bars]:
    out = {}
    for k, s in enumerate(SYMS):
        b = make_minute_bars(n_days=days, seed=k, symbol=s, rank=k + 1)
        b.n = np.round(b.qv / 1000.0)
        b.funding_t = b.t[:: 8 * 60][1:] + 0  # every 8 hours
        b.funding_rate = np.full(b.funding_t.size, 1e-4 * (k + 1))
        out[s] = b
    return out


def fill(sd: SymbolData, b: Bars) -> None:
    for i in range(len(b)):
        sd.bars[int(b.t[i] // MIN_NS)] = (
            b.o[i], b.h[i], b.low[i], b.c[i], b.v[i], b.qv[i], b.n[i], b.tb[i]
        )  # fmt: skip
    for t, r in zip(b.funding_t, b.funding_rate, strict=True):
        sd.funding[int(t)] = float(r)
    five = b.t[b.t % (5 * MIN_NS) == 0]
    for j, t in enumerate(five):
        sd.premium[int(t)] = 1e-4 * np.sin(j / 7.0)
        for name, base in (("oi", 1e6), ("top_ls", 1.5), ("acc_ls", 2.0), ("taker_ls", 1.0)):
            sd.metrics.setdefault(name, {})[int(t)] = base * (1 + 0.01 * np.cos(j / 5.0))


def test_live_features_equal_the_research_features_on_the_same_data() -> None:
    bars = synthetic()
    live = LiveData(None, [(SYMS.index(s) + 1, s) for s in SYMS], SimClock(0), 6)  # type: ignore[arg-type]
    for s, b in bars.items():
        fill(live.data[s], b)
    t_dec = int(bars["BTCUSDT"].t[-1]) + MIN_NS
    t_dec -= t_dec % (5 * MIN_NS)
    snap = live.snapshot(t_dec)
    assert snap is not None and len(snap.frame) == len(SYMS)
    # the research path: symbols numbered alphabetically, rows at the same decision bar
    frames = []
    btc = None
    for k, s in enumerate(sorted(SYMS)):
        b = bars[s]
        sd = live.data[s]
        i = int(np.searchsorted(b.t, t_dec - MIN_NS))
        prem = sd.premium_series()
        met = sd.metrics_series()
        f = symbol_frame(b, k, (), prem, met, rows=np.array([i]))
        frames.append(f)
        if s == "BTCUSDT":
            btc = f
    ref = assemble(frames, btc)
    assert np.array_equal(ref.sym, snap.frame.sym)
    np.testing.assert_allclose(snap.frame.x, ref.x, rtol=1e-6, atol=1e-7)
    finite = np.isfinite(snap.frame.x).mean()
    assert finite > 0.9, finite  # premium, metrics, funding and cross-section are all filled
    assert [r[0] for r in snap.rows()] == sorted(SYMS)


def test_series_timing_matches_the_research() -> None:
    sd = SymbolData("BTCUSDT", 1)
    sd.premium[0] = 0.5
    for name in ("oi", "top_ls", "acc_ls"):
        sd.metrics.setdefault(name, {})[0] = 1.0
    assert sd.metrics_series() is None  # taker_ls missing: no row
    sd.metrics.setdefault("taker_ls", {})[0] = 1.0
    met = sd.metrics_series()
    prem = sd.premium_series()
    assert isinstance(met, Series) and met.avail[0] == METRICS_DELAY_NS
    assert isinstance(prem, Series) and prem.avail[0] == PREMIUM_BAR_NS


def kline(open_ms: int, close: float) -> list[Any]:
    return [open_ms, "1", "2", "0.5", str(close), "10", open_ms + 59_999, "20", 7, "4", "8", "0"]


def test_parse_klines_drops_the_open_bar() -> None:
    rows = [kline(0, 1.0), kline(60_000, 1.1), kline(120_000, 1.2)]
    out = parse_klines(rows, now_ms=150_000)
    assert sorted(out) == [0, 1]
    assert out[1] == (1.0, 2.0, 0.5, 1.1, 10.0, 20.0, 7.0, 4.0)


@dataclass
class Resp:
    data: Any

    def json(self) -> Any:
        return self.data


class FakeRest:
    """Minute bars up to the clock, 5-minute premium/metrics, funding."""

    def __init__(self, clock: SimClock) -> None:
        self.clock = clock
        self.calls: list[str] = []

    async def get(self, path: str, params: dict[str, Any], weight: int | None = None) -> Resp:
        self.calls.append(path)
        now_ms = self.clock.now_ns() // 1_000_000
        if path == "/fapi/v1/klines":
            limit = int(params["limit"])
            start = params.get("startTime")
            last = now_ms // 60_000 * 60_000  # the open bar
            # Binance: bars opening at or after startTime (minute aligned)
            first = (
                -(-int(start) // 60_000) * 60_000
                if start is not None
                else last - (limit - 1) * 60_000
            )
            opens = range(first, min(last, first + (limit - 1) * 60_000) + 1, 60_000)
            return Resp([kline(t, 100.0 + (t // 60_000) % 7) for t in opens])
        if path == "/fapi/v1/premiumIndexKlines":
            last = now_ms // 300_000 * 300_000
            return Resp(
                [[t, "0", "0", "0", "0.0001", "0", t + 299_999] for t in range(last - 3_000_000,
                                                                              last + 1, 300_000)]
            )  # fmt: skip
        if path == "/fapi/v1/fundingRate":
            return Resp([{"fundingTime": now_ms - k * 28_800_000, "fundingRate": "0.0001"}
                         for k in range(3)])  # fmt: skip
        key = {"openInterestHist": "sumOpenInterest", "takerlongshortRatio": "buySellRatio"}
        name = key.get(path.rsplit("/", 1)[-1], "longShortRatio")
        last = now_ms // 300_000 * 300_000 - 300_000
        return Resp([{name: "1.5", "timestamp": t} for t in range(last - 1_500_000, last + 1,
                                                                    300_000)])  # fmt: skip


async def test_load_and_update_through_rest() -> None:
    t0 = 1_790_000_000 * 10**9 // (300 * 10**9) * (300 * 10**9)  # on the 5-minute grid
    clock = SimClock(t0 + 30 * 10**9)
    rest = FakeRest(clock)
    live = LiveData(rest, [(1, "BTCUSDT"), (2, "ETHUSDT")], clock, history_days=1)  # type: ignore[arg-type]
    await live.load()
    sd = live.data["BTCUSDT"]
    assert sd.last_minute() == t0 // MIN_NS - 1  # the bar that closed at t0
    assert len(sd.bars) >= 1439 and sd.funding and sd.premium
    assert sd.metrics_series() is not None
    clock.advance_to(t0 + 5 * MIN_NS + 2 * 10**9)
    missing = await live.update(t0 + 5 * MIN_NS, wait_s=0)
    assert missing == [] and sd.last_minute() == (t0 + 5 * MIN_NS) // MIN_NS - 1
    snap = live.snapshot(t0 + 5 * MIN_NS)
    assert snap is not None and len(snap.frame) == 2
    with pytest.raises(ValueError, match="grid"):
        live.snapshot(t0 + MIN_NS)
