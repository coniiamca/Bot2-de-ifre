"""The paper broker's fill rules and a trade's whole life in the bot, on a simulated clock:
entry → stop and target at the exchange → target / stop / time exit (limit, then market),
unfilled entries, the journal, positions the bot does not know."""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

import aiohttp
import pytest

from quanta.bot.broker import PaperBroker
from quanta.bot.config import BotConfig, StrategyConfig
from quanta.bot.runner import Bot, Timing, journal_summary
from quanta.bot.strategy import Signal
from quanta.core.clock import SimClock
from quanta.oms.state import OrderStatus
from quanta.venues.binance_usdm.filters import SymbolRules

SYM = "BNBUSDC"
S = 10**9
RULES = SymbolRules(
    SYM,
    "TRADING",
    Decimal("0.01"),
    Decimal("0.01"),
    Decimal("0.01"),
    Decimal("10000"),
    Decimal("0.01"),
    Decimal("1000"),
    Decimal("5"),
)
SIGMA = 0.0008  # stop 3·σ·√60 ≈ 1.86 %, target 1.5·σ·√60 ≈ 0.93 %


class Market:
    def __init__(self, broker: PaperBroker, clock: SimClock) -> None:
        self.b, self.clock = broker, clock

    def quote(self, mid: float, mark: float | None = None) -> None:
        now = self.clock.now_ns()
        self.b.feed._book[SYM] = (mid - 0.01, mid + 0.01, now)
        self.b.feed._mark[SYM] = (mark if mark is not None else mid, now)
        self.b._mark(SYM)

    def trade(self, px: float) -> None:
        self.b._trade(SYM, Decimal(str(px)), self.clock.now_ns() // 1_000_000)


@pytest.fixture
async def setup(tmp_path: Path) -> tuple[Bot, PaperBroker, Market, SimClock, list[str]]:
    clock = SimClock(1_790_000_000 * S)
    cfg = BotConfig(data_dir=tmp_path, strategy=StrategyConfig(tp_k=1.5))
    async with aiohttp.ClientSession() as session:
        broker = PaperBroker(cfg, [SYM], session, clock, rules={SYM: RULES})
    lines: list[str] = []

    async def no_signals(t: int) -> list[Signal]:
        return []

    timing = Timing(hold_s=3600, entry_s=300, exit_s=300)
    bot = Bot(cfg, broker, no_signals, 60, clock=clock, timing=timing, out=lines.append)
    m = Market(broker, clock)
    m.quote(600.0)
    return bot, broker, m, clock, lines


def long() -> Signal:
    return Signal(0, "BNBUSDT", SYM, 1, 2.0, SIGMA)


async def opened(bot: Bot, b: PaperBroker, m: Market) -> None:
    res = await bot.open(long())
    assert "LONG limit" in res, res
    t = bot.trades[SYM]
    assert t.state == "entry" and b.orders[t.entry_oid or ""].price == Decimal("599.99")
    m.trade(600.0)  # at our price: a touch does not fill
    await bot.manage()
    assert bot.trades[SYM].state == "entry"
    m.trade(599.98)  # through our bid
    await bot.manage()
    assert bot.trades[SYM].state == "open"


def journal(bot: Bot) -> list[dict[str, str]]:
    with bot.journal_file.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


async def test_target_hit_books_a_maker_profit(setup: tuple) -> None:  # type: ignore[type-arg]
    bot, b, m, _clock, lines = setup
    await opened(bot, b, m)
    t = bot.trades[SYM]
    tp = b.orders[t.tp_oid or ""]
    stop = b.orders[t.stop_oid or ""]
    assert tp.price > Decimal("605") and stop.price < Decimal("590") and stop.kind == "STOP"
    m.trade(float(tp.price) + 0.01)
    await bot.manage()
    assert SYM not in bot.trades and not b.positions()
    row = journal(bot)[0]
    assert row["sebep"] == "kâr al" and float(row["kar_zarar"]) > 0 and float(row["ucret"]) == 0
    assert stop.status is OrderStatus.CANCELED  # the stop was taken down after the exit
    assert any("KAPANDI" in x for x in lines)


async def test_stop_on_the_mark_loses_about_the_risk(setup: tuple) -> None:  # type: ignore[type-arg]
    bot, b, m, _clock, _ = setup
    await opened(bot, b, m)
    risk = bot.trades[SYM].risk_usd
    stop = b.orders[bot.trades[SYM].stop_oid or ""]
    m.quote(float(stop.price) - 0.5, mark=float(stop.price) - 0.1)
    await bot.manage()
    row = journal(bot)[0]
    assert row["sebep"] == "zarar kes"
    loss = -float(row["kar_zarar"])
    assert risk * 0.95 < loss < risk * 1.2  # gap below the trigger and the taker fee
    assert float(row["R"]) < -0.95


async def test_time_exit_by_limit_then_by_market(setup: tuple) -> None:  # type: ignore[type-arg]
    bot, b, m, clock, _ = setup
    await opened(bot, b, m)
    clock.advance_to(clock.now_ns() + 3601 * S)
    m.quote(601.0)
    await bot.manage()
    t = bot.trades[SYM]
    assert t.state == "exit" and b.orders[t.tp_oid or ""].status is OrderStatus.CANCELED
    exit_order = b.orders[t.exit_oid or ""]
    assert exit_order.side == "SELL" and exit_order.price == Decimal("601.01")  # at the ask
    m.trade(601.02)
    await bot.manage()
    assert journal(bot)[0]["sebep"] == "süre"
    # second trade: nobody takes our exit → market order after the exit window
    clock.advance_to(clock.now_ns() + 10 * S)
    await opened_again(bot, b, m)
    clock.advance_to(clock.now_ns() + 3601 * S)
    await bot.manage()
    clock.advance_to(clock.now_ns() + 301 * S)
    await bot.manage()
    rows = journal(bot)
    assert rows[1]["sebep"] == "süre (piyasa emri)" and float(rows[1]["ucret"]) > 0
    s = journal_summary(bot.journal_file)
    assert s["islem"] == 2 and s["sebepler"] == {"süre": 1, "süre (piyasa emri)": 1}


async def opened_again(bot: Bot, b: PaperBroker, m: Market) -> None:
    m.quote(601.0)
    res = await bot.open(Signal(300 * S, "BNBUSDT", SYM, 1, 2.0, SIGMA))
    assert "LONG" in res, res
    m.trade(600.98)
    await bot.manage()
    assert bot.trades[SYM].state == "open"


async def test_an_entry_nobody_fills_is_cancelled(setup: tuple) -> None:  # type: ignore[type-arg]
    bot, b, _m, clock, _ = setup
    await bot.open(long())
    oid = bot.trades[SYM].entry_oid or ""
    clock.advance_to(clock.now_ns() + 301 * S)
    await bot.manage()
    assert SYM not in bot.trades and b.orders[oid].status is OrderStatus.CANCELED
    assert not bot.journal_file.exists()  # no trade, nothing booked


async def test_post_only_and_paper_rules(setup: tuple) -> None:  # type: ignore[type-arg]
    _bot, b, m, _clock, _ = setup
    # a buy at the ask would take liquidity: refused like Binance's -5022
    oid, err = await b.limit(SYM, "BUY", Decimal("1"), Decimal("600.01"), "k1", reduce=False)
    assert oid is None and "-5022" in (err or "")
    oid, err = await b.limit(SYM, "SELL", Decimal("1"), Decimal("601"), "k2", reduce=True)
    assert oid is None and "azaltılacak" in (err or "")
    oid, _ = await b.limit(SYM, "BUY", Decimal("1"), Decimal("599.9"), "k3", reduce=False)
    assert (await b.limit(SYM, "BUY", Decimal("1"), Decimal("599"), "k3", reduce=False))[0] is None
    m.trade(599.89)
    assert b.positions() == {SYM: Decimal("1")} and b.order(oid or "").status is OrderStatus.FILLED
    m.quote(610.0)
    assert b.equity == pytest.approx(5000 + (609.99 + 610.01) / 2 - 599.9, abs=0.05)
    assert await b.close(SYM, "c1") and not b.positions()


async def test_a_position_the_bot_does_not_know_is_closed(setup: tuple) -> None:  # type: ignore[type-arg]
    bot, b, m, _clock, lines = setup
    await b.limit(SYM, "BUY", Decimal("1"), Decimal("599.9"), "manual", reduce=False)
    m.trade(599.0)
    assert b.positions()
    await bot.manage()
    assert not b.positions() and any("bilmediği" in x for x in lines)


async def test_daily_loss_stops_new_entries(setup: tuple) -> None:  # type: ignore[type-arg]
    bot, b, _m, _clock, _ = setup
    b.daily_guard()
    b.balance -= Decimal("200")  # −4 % of 5,000
    b.daily_guard()
    assert "günlük zarar" in (b.entry_block() or "")
    assert "giriş yok" in await bot.open(long())
