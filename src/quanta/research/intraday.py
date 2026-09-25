"""Minute-bar trade engine for intraday hypotheses (between the hourly Tier-0 test and a full
order-book replay). A strategy emits :class:`Signals` on one symbol's 1-minute bars; the
engine turns each into at most one trade, one position per symbol at a time, with
deliberately pessimistic fill rules:

* A decision on bar ``i`` (known at its close) trades at the earliest in bar ``i + 1 + latency``.
* **Market entry** at that bar's open ± slippage, taker fee.
* **Limit entry** fills only if the price trades *through* the limit (a touch is not enough:
  our place in the queue is unknown); at the open when the bar opens beyond it. Maker fee.
  Not filled within its validity → no trade.
* **Stop-loss** is a stop-market: at the stop, or at the open when the bar gaps beyond it,
  minus ``stop_slip_mult`` × slippage, taker fee. It is checked from the entry bar on.
* **Take-profit** is a resting limit: filled only when traded through, from the bar *after*
  the entry bar. Maker fee.
* If stop and target are both reached in the same bar, the stop counts.
* **Time stop** at the open ``hold`` bars after entry (market). A data gap or the end of data
  forces an exit at the last close with ``forced_mult`` × slippage.
* ``post_only`` signals (model M1, zero maker fee): the limit order rests from bar
  ``i + 1 + latency`` at ``min(limit, open)`` for a buy (``max`` for a sell; a post-only order
  never crosses the book) and fills only when a later trade goes through it, at exactly that
  price and only in a bar with volume.
* ``exit_valid`` > 0: the time exit is a post-only limit at the last close (not better than
  the exit bar's open), resting for ``exit_valid`` bars under the same fill rule; the stop
  still counts during that window and wins ties; unfilled → market at the next open.
* **Funding** at every funding time between entry and exit (longs pay positive rates).
* A liquidation flag is raised when the adverse excursion reaches 30 % (isolated, ≤ 3×).

:func:`combine` then applies the portfolio rules (risk per trade, caps, max positions) across
symbols in time order and :func:`daily_pnl` books each accepted trade on its exit day.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
from numpy.typing import NDArray

from quanta.research.minute import MIN_NS, Bars

Floats = NDArray[np.float64]
Ints = NDArray[np.int64]
DAY_NS = 86400 * 10**9
HALF_MIN_NS = MIN_NS // 2
LIQUIDATION_MOVE = 0.30
REASONS = ("tp", "sl", "time", "gap")
TP, SL, TIME, GAP = range(4)


@dataclass(frozen=True, slots=True)
class IntradayCosts:
    taker_bps: float = 5.0  # Binance USDⓈ-M VIP0
    maker_bps: float = 2.0
    slip_bps: tuple[float, float, float, float] = (1.0, 3.0, 5.0, 8.0)  # rank 1–2/3–5/6–10/11+
    stop_slip_mult: float = 2.0
    forced_mult: float = 3.0
    stress: float = 1.0  # multiplies fees and slippage
    fee_mult: float = 1.0  # e.g. 0.9 with the BNB fee discount
    through_bps: float = 0.0  # extra distance a limit price must be traded through

    def slip(self, rank: int) -> float:
        tier = 0 if rank <= 2 else 1 if rank <= 5 else 2 if rank <= 10 else 3
        return self.slip_bps[tier] * self.stress / 1e4

    @property
    def taker(self) -> float:
        return self.taker_bps * self.fee_mult * self.stress / 1e4

    @property
    def maker(self) -> float:
        return self.maker_bps * self.fee_mult * self.stress / 1e4

    def but(self, **kw: Any) -> IntradayCosts:
        return replace(self, **kw)


@dataclass(slots=True)
class Signals:
    """Candidate trades on one symbol, sorted by decision bar ``i``."""

    i: Ints
    side: NDArray[np.int8]  # +1 long, −1 short
    limit: Floats  # limit entry price; NaN = market entry
    sl: Floats  # stop price; NaN = none
    tp: Floats  # take-profit price; NaN = none
    hold: Ints  # maximum holding time in bars
    valid: int = 5  # limit validity in bars
    post_only: bool = False  # resting limit that never crosses the book (see module doc)
    exit_valid: int = 0  # > 0: time exit by a post-only limit resting this many bars

    @staticmethod
    def build(
        i: Ints,
        side: NDArray[Any],
        limit: Floats,
        sl: Floats,
        tp: Floats,
        hold: Ints | int,
        valid: int = 5,
        post_only: bool = False,
        exit_valid: int = 0,
    ) -> Signals:
        order = np.argsort(i, kind="stable")
        h = np.broadcast_to(np.asarray(hold, dtype=np.int64), i.shape)
        return Signals(
            np.asarray(i, dtype=np.int64)[order],
            np.asarray(side, dtype=np.int8)[order],
            np.asarray(limit, dtype=np.float64)[order],
            np.asarray(sl, dtype=np.float64)[order],
            np.asarray(tp, dtype=np.float64)[order],
            np.array(h[order], dtype=np.int64),
            valid,
            post_only,
            exit_valid,
        )

    def __len__(self) -> int:
        return int(self.i.size)


_TRADE_FIELDS = (
    "side",
    "entry_t",
    "exit_t",
    "entry_px",
    "exit_px",
    "reason",
    "rank",
    "gross",
    "fee",
    "slip",
    "funding",
    "net",
    "risk",
    "liq",
)


@dataclass(slots=True)
class Trades:
    """Closed trades (structure of arrays). Returns are fractions of the trade's notional:
    ``gross`` includes slippage (fill prices), ``net = gross − fee − funding``."""

    side: NDArray[np.int8]
    entry_t: Ints
    exit_t: Ints
    entry_px: Floats
    exit_px: Floats
    reason: NDArray[np.int8]
    rank: NDArray[np.int16]
    gross: Floats
    fee: Floats
    slip: Floats
    funding: Floats
    net: Floats
    risk: Floats  # stop distance / entry price (sizing)
    liq: NDArray[np.bool_]
    symbol: NDArray[np.int32] = field(default_factory=lambda: np.zeros(0, dtype=np.int32))

    def __len__(self) -> int:
        return int(self.entry_t.size)

    @staticmethod
    def from_rows(rows: list[tuple[Any, ...]]) -> Trades:
        dtypes = (
            np.int8,
            np.int64,
            np.int64,
            np.float64,
            np.float64,
            np.int8,
            np.int16,
            np.float64,
            np.float64,
            np.float64,
            np.float64,
            np.float64,
            np.float64,
            np.bool_,
        )
        data = list(zip(*rows, strict=True)) if rows else [()] * len(dtypes)
        cols: dict[str, Any] = {
            f: np.array(c, dtype=d) for f, c, d in zip(_TRADE_FIELDS, data, dtypes, strict=True)
        }
        return Trades(**cols, symbol=np.zeros(len(rows), dtype=np.int32))

    def take(self, idx: NDArray[Any]) -> Trades:
        cols: dict[str, Any] = {f: getattr(self, f)[idx] for f in _TRADE_FIELDS}
        return Trades(**cols, symbol=self.symbol[idx])

    def with_symbol(self, sym: int) -> Trades:
        self.symbol = np.full(len(self), sym, dtype=np.int32)
        return self

    @staticmethod
    def concat(parts: list[Trades]) -> Trades:
        parts = [p for p in parts if len(p)] or [Trades.from_rows([])]
        cols: dict[str, Any] = {
            f: np.concatenate([getattr(p, f) for p in parts]) for f in _TRADE_FIELDS
        }
        return Trades(**cols, symbol=np.concatenate([p.symbol for p in parts]))


def _first(mask: NDArray[np.bool_]) -> int:
    """Index of the first True, or -1."""
    if not mask.size:
        return -1
    k = int(np.argmax(mask))
    return k if mask[k] else -1


def run_trades(
    bars: Bars,
    sig: Signals,
    costs: IntradayCosts,
    *,
    max_rank: int = 10**4,
    latency: int = 0,
) -> Trades:
    """Simulate the signals of one symbol; signals whose decision bar has a universe rank of
    0 or above ``max_rank`` are ignored."""
    n = len(bars)
    if n == 0 or len(sig) == 0:
        return Trades.from_rows([])
    minute, o, h, lo, c, vol = bars.minute, bars.o, bars.h, bars.low, bars.c, bars.v
    ft, frate = bars.funding_t, bars.funding_rate
    through = costs.through_bps / 1e4
    rows: list[tuple[Any, ...]] = []
    rank_at = bars.rank[np.clip(sig.i, 0, n - 1)]
    ok = (rank_at > 0) & (rank_at <= max_rank) & (sig.i >= 0) & (sig.i < n)
    busy = -1  # exit bar of the open position
    for k in np.flatnonzero(ok):
        i = int(sig.i[k])
        if i < busy:
            continue
        j0 = i + 1 + latency
        if j0 >= n or minute[j0] != minute[i] + 1 + latency:
            continue  # no bar to trade in (data gap)
        s = int(sig.side[k])
        rank = int(rank_at[k])
        slip = costs.slip(rank)
        lim = sig.limit[k]
        if math.isnan(lim):
            e = j0
            entry = o[e] * (1.0 + s * slip)
            fee_in, slip_in, t_in = costs.taker, slip, int(bars.t[e])
        else:
            w_end = min(j0 + sig.valid, n)
            contiguous = minute[j0:w_end] == minute[j0] + np.arange(w_end - j0)
            if sig.post_only:  # rests from bar j0 without crossing the book
                lim = min(lim, o[j0]) if s > 0 else max(lim, o[j0])
            if s > 0:
                hit = (lo[j0:w_end] < lim * (1.0 - through)) & contiguous
            else:
                hit = (h[j0:w_end] > lim * (1.0 + through)) & contiguous
            if sig.post_only:
                hit &= vol[j0:w_end] > 0
            f = _first(np.logical_and.accumulate(contiguous) & hit)
            if f < 0:
                continue  # never traded through: not filled
            e = j0 + f
            # a resting order fills at its price; an ordinary limit at the open when gapped
            gapped = min(lim, o[e]) if s > 0 else max(lim, o[e])
            entry = lim if sig.post_only else gapped
            fee_in, slip_in, t_in = costs.maker, 0.0, int(bars.t[e]) + HALF_MIN_NS
        stop, target = sig.sl[k], sig.tp[k]
        hold = int(sig.hold[k])
        m_exit = sig.exit_valid
        end = min(e + hold + m_exit, n - 1)  # bar of the (last) time exit, or the last bar
        run = minute[e : end + 1] == minute[e] + np.arange(end + 1 - e)
        g = _first(~run)
        last = e + g - 1 if g >= 0 else end  # last bar we can observe in a row
        window = slice(e, last + 1)
        if s > 0:
            sl_hit = lo[window] <= stop if not math.isnan(stop) else np.zeros(last + 1 - e, bool)
            tp_hit = h[window] > target * (1.0 + through) if not math.isnan(target) else None
        else:
            sl_hit = h[window] >= stop if not math.isnan(stop) else np.zeros(last + 1 - e, bool)
            tp_hit = lo[window] < target * (1.0 - through) if not math.isnan(target) else None
        timed = last >= e + hold  # the time-exit bar exists and follows in a row
        scan = hold if timed else last + 1 - e  # bars before the time exit
        fs = _first(sl_hit[:scan])
        ftp = -1
        if tp_hit is not None:
            tp_hit = tp_hit[:scan].copy()
            tp_hit[0] = False  # never in the entry bar
            ftp = _first(tp_hit)
        fx = fsx = -1  # maker time exit: first fill / first stop in its window
        x0 = e + hold
        if timed and m_exit > 0 and not (fs >= 0 or ftp >= 0):
            ex = min(x0 + m_exit, last + 1)
            ref = c[x0 - 1]
            px_exit = max(ref, o[x0]) if s > 0 else min(ref, o[x0])  # never crosses the book
            if s > 0:
                mk = h[x0:ex] > px_exit * (1.0 + through)
            else:
                mk = lo[x0:ex] < px_exit * (1.0 - through)
            fx = _first(mk & (vol[x0:ex] > 0))
            fsx = _first(sl_hit[x0 - e : ex - e])
            if fsx >= 0 and (fx < 0 or fsx <= fx):
                fs, fx = hold + fsx, -1  # the stop fires while the exit order rests
        if fs >= 0 and (ftp < 0 or fs <= ftp):
            x = e + fs
            # the bar's open counts (gap through the stop) unless we were filled inside it
            at_open = x > e or math.isnan(lim)
            px = (min(stop, o[x]) if s > 0 else max(stop, o[x])) if at_open else stop
            sslip = slip * costs.stop_slip_mult
            exit_px = px * (1.0 - s * sslip)
            fee_out, slip_out, t_out, why = costs.taker, sslip, int(bars.t[x]) + HALF_MIN_NS, SL
        elif ftp >= 0:
            x = e + ftp
            exit_px = max(target, o[x]) if s > 0 else min(target, o[x])
            fee_out, slip_out, t_out, why = costs.maker, 0.0, int(bars.t[x]) + HALF_MIN_NS, TP
        elif fx >= 0:
            x = x0 + fx
            exit_px = px_exit
            fee_out, slip_out, t_out, why = costs.maker, 0.0, int(bars.t[x]) + HALF_MIN_NS, TIME
        elif timed and last >= x0 + m_exit:
            x = x0 + m_exit
            exit_px = o[x] * (1.0 - s * slip)
            fee_out, slip_out, t_out, why = costs.taker, slip, int(bars.t[x]), TIME
        else:
            x = last
            fslip = slip * costs.forced_mult
            exit_px = c[x] * (1.0 - s * fslip)
            fee_out, slip_out, t_out, why = costs.taker, fslip, int(bars.t[x]) + MIN_NS, GAP
        busy = x
        a, b = np.searchsorted(ft, t_in, "right"), np.searchsorted(ft, t_out, "right")
        funding = s * float(frate[a:b].sum())
        if s > 0:
            adverse = 1.0 - float(lo[e : x + 1].min()) / entry
        else:
            adverse = float(h[e : x + 1].max()) / entry - 1.0
        gross = s * (exit_px / entry - 1.0)
        fee = fee_in + fee_out
        risk = abs(entry - stop) / entry if not math.isnan(stop) else math.nan
        rows.append(
            (
                s,
                t_in,
                t_out,
                entry,
                exit_px,
                why,
                rank,
                gross,
                fee,
                slip_in + slip_out,
                funding,
                gross - fee - funding,
                risk,
                adverse >= LIQUIDATION_MOVE,
            )
        )
    return Trades.from_rows(rows)


@dataclass(frozen=True, slots=True)
class Portfolio:
    """Plan §13.4: risk per trade 0.5 % of equity, ≤ 1× equity per coin, ≤ 1.5× gross,
    at most 3 positions at a time."""

    risk_per_trade: float = 0.005
    cap_symbol: float = 1.0
    cap_gross: float = 1.5
    max_positions: int = 3
    default_risk: float = 0.01  # stop distance assumed for a trade without a stop


def combine(trades: Trades, pf: Portfolio) -> tuple[Trades, Floats]:
    """Accept trades in entry-time order under the portfolio limits; returns the accepted
    trades and their weights (notional as a fraction of equity)."""
    if not len(trades):
        return trades, np.zeros(0)
    order = np.lexsort((trades.symbol, trades.entry_t))
    risk = np.where(np.isfinite(trades.risk) & (trades.risk > 0), trades.risk, pf.default_risk)
    want = np.minimum(pf.risk_per_trade / risk, pf.cap_symbol)
    open_: list[tuple[int, float]] = []  # (exit time, weight)
    gross = 0.0
    keep: list[int] = []
    weights: list[float] = []
    for k in order:
        t = int(trades.entry_t[k])
        while open_ and open_[0][0] <= t:
            gross -= heapq.heappop(open_)[1]
        w = float(want[k])
        if len(open_) >= pf.max_positions or gross + w > pf.cap_gross + 1e-12:
            continue
        heapq.heappush(open_, (int(trades.exit_t[k]), w))
        gross += w
        keep.append(int(k))
        weights.append(w)
    idx = np.array(keep, dtype=np.int64)
    return trades.take(idx), np.array(weights)


def daily_pnl(trades: Trades, weights: Floats, first_day: int, end_day: int) -> Floats:
    """Net return per UTC day in [first_day, end_day) (day numbers since 1970), each trade
    booked on its exit day."""
    days = trades.exit_t // DAY_NS - first_day
    ok = (days >= 0) & (days < end_day - first_day)
    return np.bincount(
        days[ok].astype(np.int64), weights=(weights * trades.net)[ok], minlength=end_day - first_day
    ).astype(np.float64)


def ewma(x: Floats, halflife: float) -> Floats:
    """Exponentially weighted mean of a 1-D series, point in time (value ``t`` uses ``x[:t+1]``).
    NaN values leave the mean unchanged. Vectorised in blocks: y_j = q^(j+1)·y_prev +
    a·q^j·Σ_{m≤j} q^(−m)·x_m, with the block short enough for q^(−j) to stay finite."""
    a = 1.0 - 0.5 ** (1.0 / halflife)
    q = 1.0 - a
    x = np.asarray(x, dtype=np.float64)
    out = np.full(x.size, np.nan)
    valid = np.isfinite(x)
    if not valid.any():
        return out
    first = int(np.argmax(valid))
    block = max(1, int(20.0 / -math.log(q)))
    prev = float(x[first])
    pos = first
    while pos < x.size:
        seg = x[pos : pos + block]
        m = np.isfinite(seg)
        if m.all():
            j = np.arange(seg.size, dtype=np.float64)
            y = q ** (j + 1.0) * prev + a * q**j * np.cumsum(seg * q ** (-j))
        else:  # rare (gaps): plain recursion for this block
            y = np.empty(seg.size)
            cur = prev
            for u in range(seg.size):
                if m[u]:
                    cur = q * cur + a * seg[u]
                y[u] = cur
        out[pos : pos + seg.size] = y
        prev = float(y[-1])
        pos += seg.size
    return out


def rolling_sum(x: Floats, k: int) -> Floats:
    """Sum of the last ``k`` values (NaN for the first k − 1)."""
    cs = np.concatenate([[0.0], np.cumsum(np.nan_to_num(np.asarray(x, dtype=np.float64)))])
    out = np.full(x.size, np.nan)
    if x.size >= k:
        out[k - 1 :] = cs[k:] - cs[:-k]
    return out


def contiguous(bars: Bars, k: int) -> NDArray[np.bool_]:
    """True where the last ``k`` + 1 bars (i − k … i) are consecutive minutes."""
    m = bars.minute
    out = np.zeros(m.size, dtype=bool)
    if m.size > k:
        out[k:] = m[k:] - m[:-k] == k
    return out
