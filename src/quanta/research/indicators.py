"""Classic indicator strategies (``research/prereg/T1.yaml``): the user asked "indikatörler
kullan". Six of the most used retail rules, each on 5- and 15-minute bars of the USDC
contracts, all with the same exits so that only the entry signal differs:

* ``rsi``        RSI(14) crosses below 30 → long, above 70 → short (mean reversion)
* ``bollinger``  close leaves the Bollinger band (20, 2) below → long, above → short
* ``ema_cross``  EMA(9) crosses EMA(21) up → long, down → short (trend)
* ``macd``       MACD(12, 26) crosses its signal line (9) up → long, down → short
* ``supertrend`` Supertrend(10, 3) turns up → long, down → short
* ``trend_rsi``  above EMA(200) and RSI(14) dips below 35 → long; below EMA(200) and RSI
  rises above 65 → short (pullback in the trend)

Bars of ``tf`` minutes are built from the 1-minute bars (complete bars only); a signal is
known at the bar's close and becomes an order on the 1-minute bar that closes it. Entry: a
post-only limit at that close (maker, resting 5 minutes); exits by the minute engine: a maker
take-profit and a stop-market both ``k_atr`` · ATR(14) away (equal distances, the user's
"1.100 / 900"), and a time exit after ``hold_bars`` bars. One position per coin.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from quanta.research.intraday import Signals, ewma
from quanta.research.minute import Bars
from quanta.research.report import num

Floats = NDArray[np.float64]
Ints = NDArray[np.int64]
RULES = ("rsi", "bollinger", "ema_cross", "macd", "supertrend", "trend_rsi")
RULE_TR = {
    "rsi": "RSI 30/70",
    "bollinger": "Bollinger (20, 2)",
    "ema_cross": "EMA 9/21 kesişimi",
    "macd": "MACD kesişimi",
    "supertrend": "Supertrend (10, 3)",
    "trend_rsi": "EMA200 trend + RSI geri çekilme",
}
WARMUP_BARS = 250  # EMA(200) and friends settle first


@dataclass(slots=True)
class TfBars:
    """``tf``-minute bars; ``close_idx``: the 1-minute bar that closes each one."""

    close_idx: Ints
    o: Floats
    h: Floats
    low: Floats
    c: Floats
    complete: NDArray[np.bool_]


def resample(bars: Bars, tf: int) -> TfBars:
    m = bars.minute
    g = m // tf
    starts = np.flatnonzero(np.r_[True, g[1:] != g[:-1]])
    ends = np.r_[starts[1:], m.size] - 1
    count = ends - starts + 1
    complete = (count == tf) & (m[ends] - m[starts] == tf - 1) & (m[ends] % tf == tf - 1)
    return TfBars(
        ends.astype(np.int64),
        bars.o[starts],
        np.maximum.reduceat(bars.h, starts),
        np.minimum.reduceat(bars.low, starts),
        bars.c[ends],
        complete,
    )


# ---------------------------------------------------------------- indicators


def ema(x: Floats, n: int) -> Floats:
    """Exponential moving average with α = 2 / (n + 1), started at the first value."""
    alpha = 2.0 / (n + 1.0)
    return ewma(x, math.log(0.5) / math.log(1.0 - alpha))


def wilder(x: Floats, n: int) -> Floats:
    """Wilder's smoothing (α = 1 / n), as in RSI and ATR."""
    return ewma(x, math.log(0.5) / math.log(1.0 - 1.0 / n))


def rsi(c: Floats, n: int = 14) -> Floats:
    d = np.diff(c, prepend=c[0])
    up, down = wilder(np.maximum(d, 0.0), n), wilder(np.maximum(-d, 0.0), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        out: Floats = np.where(down > 0, 100.0 - 100.0 / (1.0 + up / down), 100.0)
    out[:n] = np.nan
    return out


def bollinger(c: Floats, n: int = 20, k: float = 2.0) -> tuple[Floats, Floats, Floats]:
    cs = np.concatenate([[0.0], np.cumsum(c)])
    cs2 = np.concatenate([[0.0], np.cumsum(c * c)])
    mean = np.full(c.size, np.nan)
    sd = np.full(c.size, np.nan)
    if c.size >= n:
        s = cs[n:] - cs[:-n]
        s2 = cs2[n:] - cs2[:-n]
        mean[n - 1 :] = s / n
        sd[n - 1 :] = np.sqrt(np.maximum(s2 / n - (s / n) ** 2, 0.0))
    return mean - k * sd, mean, mean + k * sd


def atr(h: Floats, low: Floats, c: Floats, n: int = 14) -> Floats:
    prev = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - low, np.maximum(np.abs(h - prev), np.abs(low - prev)))
    out = wilder(tr, n)
    out[:n] = np.nan
    return out


def supertrend(h: Floats, low: Floats, c: Floats, n: int = 10, k: float = 3.0) -> NDArray[np.int8]:
    """Direction (+1 up, −1 down) of the classic Supertrend."""
    a = atr(h, low, c, n)
    mid = (h + low) / 2.0
    up_band, dn_band = mid - k * a, mid + k * a
    direction = np.zeros(c.size, dtype=np.int8)
    final_up, final_dn = np.nan, np.nan
    d = 1
    for i in range(c.size):
        if not math.isfinite(a[i]):
            continue
        ub, db = up_band[i], dn_band[i]
        if math.isfinite(final_up) and c[i - 1] > final_up:
            ub = max(ub, final_up)
        if math.isfinite(final_dn) and c[i - 1] < final_dn:
            db = min(db, final_dn)
        if d > 0 and c[i] < ub:
            d = -1
        elif d < 0 and c[i] > db:
            d = 1
        final_up, final_dn = ub, db
        direction[i] = d
    return direction


def _cross_up(a: Floats, b: Floats) -> NDArray[np.bool_]:
    out = np.zeros(a.size, dtype=bool)
    out[1:] = (a[:-1] <= b[:-1]) & (a[1:] > b[1:])
    return out


def rule_side(rule: str, x: TfBars) -> NDArray[np.int8]:
    """+1 / −1 at the bars where ``rule`` signals (known at the bar's close), else 0."""
    c = x.c
    side = np.zeros(c.size, dtype=np.int8)
    if rule in ("rsi", "trend_rsi"):
        r = rsi(c)
        lo, hi = (30.0, 70.0) if rule == "rsi" else (35.0, 65.0)
        longs = _cross_up(np.full(c.size, lo), r)  # RSI goes below lo
        shorts = _cross_up(r, np.full(c.size, hi))  # RSI goes above hi
        if rule == "trend_rsi":
            e200 = ema(c, 200)
            longs &= c > e200
            shorts &= c < e200
    elif rule == "bollinger":
        lower, _, upper = bollinger(c)
        longs = _cross_up(lower, c)  # close falls below the lower band
        shorts = _cross_up(c, upper)  # close rises above the upper band
    elif rule == "ema_cross":
        fast, slow = ema(c, 9), ema(c, 21)
        longs, shorts = _cross_up(fast, slow), _cross_up(slow, fast)
    elif rule == "macd":
        line = ema(c, 12) - ema(c, 26)
        sig = ema(line, 9)
        longs, shorts = _cross_up(line, sig), _cross_up(sig, line)
    elif rule == "supertrend":
        d = supertrend(x.h, x.low, c)
        longs = np.r_[False, (d[:-1] < 0) & (d[1:] > 0)]
        shorts = np.r_[False, (d[:-1] > 0) & (d[1:] < 0)]
    else:
        raise ValueError(f"unknown rule {rule!r}")
    side[longs] = 1
    side[shorts] = -1
    return side


# ---------------------------------------------------------------- the setting


@dataclass(frozen=True, slots=True)
class IndicatorParams:
    rule: str
    tf: int  # bar length in minutes
    k_atr: float = 1.5  # take-profit and stop distance in ATR(14) of the tf bars
    hold_bars: int = 12  # time exit after this many tf bars
    valid: int = 5  # the entry limit rests this many minutes
    exit_valid: int = 5

    @staticmethod
    def of(cfg: dict[str, Any], fixed: dict[str, Any]) -> IndicatorParams:
        merged = {**fixed.get("params", {}), **cfg}
        names = IndicatorParams.__dataclass_fields__
        p = IndicatorParams(**{k: v for k, v in merged.items() if k in names})
        if p.rule not in RULES:
            raise ValueError(f"unknown rule {p.rule!r}")
        return p

    def label(self) -> str:
        return f"{RULE_TR[self.rule]}, {self.tf} dk mum, ±{num(self.k_atr, 1)}·ATR"

    def signals(self, bars: Bars) -> Signals:
        empty = Signals.build(
            np.zeros(0, np.int64), np.zeros(0), np.zeros(0), np.zeros(0), np.zeros(0), 1
        )
        x = resample(bars, self.tf)
        if x.c.size <= WARMUP_BARS:
            return empty
        side = rule_side(self.rule, x)
        a = atr(x.h, x.low, x.c)
        k = np.arange(x.c.size)
        ok = (side != 0) & x.complete & (k >= WARMUP_BARS) & np.isfinite(a) & (a > 0)
        ok &= bars.rank[x.close_idx] > 0
        ok &= x.close_idx + self.valid < len(bars)
        k = k[ok]
        i, s, px, dist = x.close_idx[k], side[k], x.c[k], self.k_atr * a[k]
        return Signals.build(
            i,
            s,
            px,
            px - s * dist,
            px + s * dist,
            self.hold_bars * self.tf,
            self.valid,
            post_only=True,
            exit_valid=self.exit_valid,
        )


INDICATOR_METHOD = [
    "- Uygun USDC kontratları (önceki ay günde ≥ 20 milyon $ işlem, en az bir tam aydır listeli, "
    "en hacimli 10). 1 dakikalık mumlardan 5 / 15 dakikalık mumlar kuruldu (yalnız tam mumlar).",
    "- Kurallar: RSI(14) 30'un altına → long, 70'in üstüne → short; kapanış Bollinger (20, 2) "
    "alt bandının altına → long, üst bandın üstüne → short; EMA 9, EMA 21'i yukarı keserse long, "
    "aşağı keserse short; MACD (12, 26) sinyal çizgisini (9) yukarı / aşağı keserse long / short; "
    "Supertrend (10, 3) yukarı / aşağı dönerse long / short; EMA200 üstünde RSI 35'in altına → "
    "long, EMA200 altında RSI 65'in üstüne → short.",
    "- Sinyal mumun kapanışında bilinir; giriş o kapanışa yalnız-maker limit emir (5 dk, fiyat "
    "ötesine geçmeden dolmaz). Çıkış bütün kurallarda aynı: kâr al = zarar kes = 1,5·ATR(14) "
    "(maker kâr al, stop-market zarar kes, aynı mumda ikisi de varsa zarar kes sayılır), 12 mum "
    "sonra süre çıkışı. Coin başına aynı anda bir pozisyon. Ücret: maker %0, taker %0,04 + kayma.",
]


def indicators_markdown(doc: dict[str, Any]) -> str:
    from quanta.research.scalper import selection_markdown

    return selection_markdown(doc, INDICATOR_METHOD)


def run_indicators(
    prereg: Any,
    prereg_sha: str,
    root: Any,
    repo: Any,
    *,
    final: bool = False,
    exploratory: bool = False,
    workers: int | None = None,
) -> dict[str, Any]:
    from quanta.research.scalper import Setting, run_selection

    configs: dict[str, dict[str, Any]] = prereg.fixed["configs"]
    settings: dict[str, Setting] = {
        str(n): IndicatorParams.of(configs[str(n)], prereg.fixed) for n in prereg.grid["config"]
    }
    return run_selection(
        prereg, prereg_sha, root, repo, settings, exploratory=exploratory, workers=workers
    )
