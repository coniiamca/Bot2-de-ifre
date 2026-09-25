"""Minute-bar trade engine: fills, exits, costs, funding and portfolio limits on hand-built
bars with known answers."""

from __future__ import annotations

from typing import Any

import pytest

np = pytest.importorskip("numpy")

from quanta.research.intraday import (  # noqa: E402
    GAP,
    SL,
    TIME,
    TP,
    IntradayCosts,
    Portfolio,
    Signals,
    Trades,
    combine,
    daily_pnl,
    run_trades,
)
from quanta.research.minute import MIN_NS, Bars  # noqa: E402

T0 = 1_700_000_040 * 10**9 // MIN_NS * MIN_NS  # a minute boundary
C = IntradayCosts()
SLIP, TAKER, MAKER = 1e-4, 5e-4, 2e-4  # rank 1


def bars(
    o: list[float],
    h: list[float] | None = None,
    lo: list[float] | None = None,
    c: list[float] | None = None,
    minutes: list[int] | None = None,
    rank: int = 1,
    funding: tuple[list[int], list[float]] = ([], []),
) -> Bars:
    n = len(o)
    arr = np.array(o, dtype=float)
    mins = np.array(minutes if minutes is not None else range(n), dtype=np.int64)
    return Bars(
        "XUSDT",
        T0 + mins * MIN_NS,
        arr,
        np.array(h if h is not None else o, dtype=float),
        np.array(lo if lo is not None else o, dtype=float),
        np.array(c if c is not None else o, dtype=float),
        np.ones(n),
        np.ones(n),
        np.ones(n) / 2,
        np.full(n, rank, dtype=np.int16),
        T0 + np.array(funding[0], dtype=np.int64) * MIN_NS,
        np.array(funding[1], dtype=float),
    )


def sig(i: int, side: int = 1, **kw: Any) -> Signals:
    nan = float("nan")
    return Signals.build(
        np.array([i]),
        np.array([side]),
        np.array([kw.get("limit", nan)]),
        np.array([kw.get("sl", nan)]),
        np.array([kw.get("tp", nan)]),
        kw.get("hold", 3),
        kw.get("valid", 5),
    )


def one(b: Bars, s: Signals, **kw: Any) -> Trades:
    tr = run_trades(b, s, C, **kw)
    assert len(tr) == 1
    return tr


def test_market_round_trip_costs_and_time_exit() -> None:
    b = bars([100.0] * 8)
    tr = one(b, sig(0, hold=3))
    assert tr.reason[0] == TIME
    assert tr.entry_t[0] == b.t[1] and tr.exit_t[0] == b.t[4]  # decision bar 0 → trade in bar 1
    assert tr.entry_px[0] == pytest.approx(100 * (1 + SLIP))
    expected = (1 - SLIP) / (1 + SLIP) - 1 - 2 * TAKER
    assert tr.net[0] == pytest.approx(expected)
    assert tr.fee[0] == pytest.approx(2 * TAKER) and tr.slip[0] == pytest.approx(2 * SLIP)
    late = one(b, sig(0, hold=3), latency=1)
    assert late.entry_t[0] == b.t[2]


def test_take_profit_needs_trade_through_and_never_in_entry_bar() -> None:
    touch = bars([100.0] * 6, h=[100, 101, 101, 101, 101, 101])
    assert one(touch, sig(0, tp=101.0, hold=4)).reason[0] == TIME
    through = bars([100.0] * 6, h=[100, 102, 100, 101.01, 100, 100])
    tr = one(through, sig(0, tp=101.0, hold=4))
    assert tr.reason[0] == TP and tr.exit_px[0] == 101.0 and tr.exit_t[0] > through.t[3]
    assert tr.fee[0] == pytest.approx(TAKER + MAKER)  # bar 1 (entry bar) spike ignored


def test_stop_wins_a_bar_that_reaches_both() -> None:
    b = bars([100.0] * 6, h=[100, 100, 103, 100, 100, 100], lo=[100, 100, 97, 100, 100, 100])
    tr = one(b, sig(0, sl=98.0, tp=102.0, hold=4))
    assert tr.reason[0] == SL
    assert tr.exit_px[0] == pytest.approx(98.0 * (1 - 2 * SLIP))


def test_gap_through_the_stop_fills_at_the_worse_open() -> None:
    b = bars([100, 100, 100, 95, 95, 95], lo=[100, 100, 100, 94, 95, 95])
    tr = one(b, sig(0, sl=98.0, hold=4))
    assert tr.reason[0] == SL and tr.exit_px[0] == pytest.approx(95 * (1 - 2 * SLIP))
    short = bars([100, 100, 100, 105, 105, 105], h=[100, 100, 100, 106, 105, 105])
    ts = one(short, sig(0, side=-1, sl=102.0, hold=4))
    assert ts.exit_px[0] == pytest.approx(105 * (1 + 2 * SLIP))
    assert ts.net[0] < -0.05


def test_limit_entry_fills_only_when_traded_through() -> None:
    touch = bars([100.0] * 8, lo=[100, 99, 99, 99, 99, 99, 99, 99])
    assert len(run_trades(touch, sig(0, limit=99.0, valid=5), C)) == 0
    through = bars([100.0] * 8, lo=[100, 100, 98.9, 100, 100, 100, 100, 100])
    tr = one(through, sig(0, limit=99.0, valid=5, hold=3))
    assert tr.entry_px[0] == 99.0 and tr.fee[0] == pytest.approx(MAKER + TAKER)
    assert tr.entry_t[0] == through.t[2] + MIN_NS // 2
    gap_open = bars([100, 97, 97, 97, 97, 97, 97], lo=[100, 97, 97, 97, 97, 97, 97])
    assert one(gap_open, sig(0, limit=99.0, hold=3)).entry_px[0] == 97.0  # better: the open
    expired = bars([100.0] * 12, lo=[100] * 7 + [98] * 5)
    assert len(run_trades(expired, sig(0, limit=99.0, valid=5), C)) == 0


def test_data_gap_forces_an_exit_and_blocks_entry() -> None:
    b = bars([100, 100, 100, 100, 100], c=[100, 100, 101, 100, 100], minutes=[0, 1, 2, 10, 11])
    tr = one(b, sig(0, hold=5))
    assert tr.reason[0] == GAP and tr.exit_px[0] == pytest.approx(101 * (1 - 3 * SLIP))
    assert len(run_trades(b, sig(2, hold=2), C)) == 0  # next minute missing: no entry


def test_funding_sign_and_timing() -> None:
    b = bars([100.0] * 10, funding=([3, 20], [0.001, 0.5]))
    long_ = one(b, sig(0, hold=4))
    short = one(b, sig(0, side=-1, hold=4))
    assert long_.funding[0] == pytest.approx(0.001) and short.funding[0] == pytest.approx(-0.001)
    assert short.net[0] - long_.net[0] == pytest.approx(0.002, abs=1e-6)  # + tiny slip asymmetry
    # exit at the open of bar 5: a funding at that very moment is charged, one later is not
    at_exit = one(bars([100.0] * 10, funding=([5], [0.001])), sig(0, hold=4))
    after = one(bars([100.0] * 10, funding=([6], [0.001])), sig(0, hold=4))
    assert at_exit.funding[0] == pytest.approx(0.001) and after.funding[0] == 0.0


def test_one_position_per_symbol_and_rank_filter() -> None:
    b = bars([100.0] * 12)
    two = Signals.build(
        np.array([0, 2, 5]),
        np.array([1, 1, 1]),
        np.full(3, np.nan),
        np.full(3, np.nan),
        np.full(3, np.nan),
        3,
    )
    tr = run_trades(b, two, C)
    assert list(tr.entry_t) == [b.t[1], b.t[6]]  # the signal at bar 2 comes while in a trade
    assert len(run_trades(bars([100.0] * 12, rank=15), two, C, max_rank=10)) == 0
    assert len(run_trades(bars([100.0] * 12, rank=0), two, C)) == 0


def test_liquidation_flag_on_a_30_percent_wick() -> None:
    b = bars([100.0] * 6, lo=[100, 100, 65, 100, 100, 100])
    assert one(b, sig(0, hold=4)).liq[0]
    assert not one(b, sig(0, side=-1, hold=4)).liq[0]


def _trade(entry: int, exit_: int, risk: float, sym: int, net: float = 0.01) -> Trades:
    t = Trades.from_rows(
        [(1, entry, exit_, 100.0, 101.0, TIME, 1, net, 0.0, 0.0, 0.0, net, risk, False)]
    )
    return t.with_symbol(sym)


def test_portfolio_limits_and_daily_booking() -> None:
    day = 86400 * 10**9
    trades = Trades.concat(
        [
            _trade(10, 100, 0.01, 0),  # weight 0.5
            _trade(20, 100, 0.01, 1),
            _trade(30, 100, 0.01, 2),
            _trade(40, 100, 0.01, 3),  # a 4th position: refused
            _trade(100, day + 5, 0.002, 4),  # after the first three closed: weight capped at 1
        ]
    )
    acc, w = combine(trades, Portfolio())
    assert list(acc.symbol) == [0, 1, 2, 4] and list(w) == [0.5, 0.5, 0.5, 1.0]
    gross = combine(
        Trades.concat([_trade(10, 99, 0.004, 0), _trade(20, 99, 0.004, 1)]), Portfolio()
    )
    assert list(gross[1]) == [1.0]  # 1 + 1 > 1.5 gross cap
    pnl = daily_pnl(acc, w, 0, 2)
    assert pnl == pytest.approx([0.015, 0.01])


def maker_sig(i: int, side: int = 1, **kw: Any) -> Signals:
    nan = float("nan")
    return Signals.build(
        np.array([i]),
        np.array([side]),
        np.array([kw.get("limit", nan)]),
        np.array([kw.get("sl", nan)]),
        np.array([kw.get("tp", nan)]),
        kw.get("hold", 3),
        kw.get("valid", 5),
        post_only=True,
        exit_valid=kw.get("exit_valid", 0),
    )


def test_post_only_entry_never_crosses_and_fills_at_its_price() -> None:
    # bar 1 opens below the buy limit: an ordinary limit fills at that open (98); a post-only
    # order rests at 98 instead and needs a later trade through 98
    o = [100.0, 98.0, 98.0, 98.0, 98.0, 98.0, 98.0, 98.0]
    lo = [100.0, 98.0, 97.5, 98.0, 98.0, 98.0, 98.0, 98.0]
    ordinary = one(bars(o, lo=lo), sig(0, limit=99.0))
    assert ordinary.entry_px[0] == 98.0 and ordinary.entry_t[0] == T0 + MIN_NS + MIN_NS // 2
    b = bars(o, lo=lo)
    tr = one(b, maker_sig(0, limit=99.0))
    assert tr.entry_px[0] == 98.0 and tr.entry_t[0] == b.t[2] + MIN_NS // 2  # bar 2 trades through
    assert tr.fee[0] == pytest.approx(MAKER + TAKER)
    # a touch is not a fill, and a bar without volume never fills
    assert len(run_trades(bars(o, lo=[100.0] + [98.0] * 7), maker_sig(0, limit=99.0), C)) == 0
    quiet = bars(o, lo=lo)
    quiet.v[2] = 0.0
    assert len(run_trades(quiet, maker_sig(0, limit=99.0), C)) == 0


def test_maker_time_exit_rests_then_falls_back_to_market() -> None:
    # long filled at 99.5 in bar 1; time exit after 3 bars: post-only sell at close[3]
    o = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0]
    lo = [100.0, 99.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0]
    h = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.5, 100.0, 100.0, 100.0]
    b = bars(o, h=h, lo=lo)
    tr = one(b, maker_sig(0, limit=99.5, hold=3, exit_valid=3))
    assert tr.reason[0] == TIME and tr.exit_px[0] == 100.0  # resting at 100, bar 6 trades above
    assert tr.exit_t[0] == b.t[6] + MIN_NS // 2
    assert tr.fee[0] == pytest.approx(2 * MAKER) and tr.slip[0] == 0.0
    # never traded through within the window → market at the open after it
    flat = bars(o, lo=lo)
    tr = one(flat, maker_sig(0, limit=99.5, hold=3, exit_valid=3))
    assert tr.exit_t[0] == flat.t[7] and tr.exit_px[0] == pytest.approx(100 * (1 - SLIP))
    assert tr.fee[0] == pytest.approx(MAKER + TAKER)
    # the stop still counts while the exit order rests, and wins a tie in the same bar
    lo2 = [100.0, 99.0, 100.0, 100.0, 100.0, 100.0, 97.0, 100.0, 100.0, 100.0]
    stopped = one(bars(o, h=h, lo=lo2), maker_sig(0, limit=99.5, sl=98.0, hold=3, exit_valid=3))
    assert stopped.reason[0] == SL and stopped.exit_t[0] == b.t[6] + MIN_NS // 2
    # a gap during the exit window forces the usual exit at the last close
    gap = bars(o[:6], lo=lo[:6], minutes=[0, 1, 2, 3, 4, 5])
    assert one(gap, maker_sig(0, limit=99.5, hold=3, exit_valid=3)).reason[0] == GAP


def test_default_signals_keep_the_old_rules() -> None:
    b = bars([100.0] * 8, lo=[100.0, 98.0] + [100.0] * 6)
    old = one(b, sig(0, limit=99.0))
    assert old.entry_px[0] == 99.0 and old.reason[0] == TIME and old.exit_t[0] == b.t[4]
