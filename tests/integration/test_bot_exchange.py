"""The bot end to end against the fake trading venue (the demo/live path: the trader's OMS,
risk checks, kill switch and user stream): account set-up for a USDC contract, a trade
stopped out at the exchange, a time exit that ends with a market order, the maker-fee guard,
Multi-Assets refusal, Ctrl+C closing positions and resuming saved trades after a restart."""

from __future__ import annotations

import asyncio
import csv
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

import aiohttp
import pytest

from quanta.bot.broker import ExchangeBroker, exchange_config
from quanta.bot.config import BotConfig, StrategyConfig
from quanta.bot.runner import Bot, Timing
from quanta.bot.strategy import Signal
from quanta.risk.killswitch import Level
from quanta.risk.limits import RiskLimits
from quanta.venues.binance_usdm.signing import HmacSigner
from tests.integration.fake_trading import FakeTrading

SYM = "BNBUSDC"
S = 10**9


@pytest.fixture
async def venue() -> AsyncIterator[FakeTrading]:
    fv = FakeTrading(symbols=(SYM,), price=Decimal("600"), quote="USDC", maker_fee="0")
    await fv.start()
    yield fv
    await fv.stop()


async def until(pred: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.05)


def bot_config(tmp: Path, **kw: object) -> BotConfig:
    return BotConfig(
        mode="demo",
        data_dir=tmp,
        strategy=StrategyConfig(tp_k=1.5),
        limits=RiskLimits(
            max_order_notional_usdt=5000,
            max_new_positions_per_hour=12,
            stale_stream_s=1.0,
            price_band=0.02,
        ),
        **kw,  # type: ignore[arg-type]
    )


@asynccontextmanager
async def running(
    venue: FakeTrading, tmp: Path, timing: Timing | None = None, **kw: object
) -> AsyncIterator[tuple[Bot, ExchangeBroker, list[str]]]:
    cfg = bot_config(tmp, **kw)
    tcfg = exchange_config(cfg, [SYM]).model_copy(
        update={
            "rest_url": venue.rest_url,
            "ws_url": venue.ws_url,
            "reconcile_every_s": 1.0,
            "account_every_s": 0.3,
            "deadman_renew_s": 0.3,
            "loop_s": 0.1,
        }
    )
    lines: list[str] = []

    async def no_signals(t: int) -> list[Signal]:
        return []

    async with aiohttp.ClientSession() as session:
        broker = ExchangeBroker(tcfg, session, HmacSigner("test-key", "test-secret"))
        bot = Bot(
            cfg,
            broker,
            no_signals,
            60,
            timing=timing or Timing(grid_s=3600, hold_s=3600, entry_s=30, exit_s=30, loop_s=0.1),
            out=lines.append,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(bot.run(stop))
        try:
            t = broker.t
            await until(lambda: t.ready and t.market.view(SYM).mark > 0 and t.user.connected)
            await until(lambda: t.kill.level is Level.ACTIVE or t.kill.sticky > Level.ACTIVE)
            yield bot, broker, lines
        finally:
            stop.set()
            await task


def long(t_s: int = 300) -> Signal:
    return Signal(t_s * S, "BNBUSDT", SYM, 1, 2.0, 0.0008)


async def fill_entry(venue: FakeTrading, bot: Bot) -> None:
    await until(lambda: bool(venue.open_orders_of(SYM)))
    entry = venue.open_orders_of(SYM)[0]
    assert entry.tif == "GTX" and entry.side == "BUY" and not entry.reduce_only
    venue.set_price(SYM, float(entry.price) - 0.1)  # the ask comes down to our bid
    await until(lambda: SYM in bot.trades and bot.trades[SYM].state == "open")


def journal(bot: Bot) -> list[dict[str, str]]:
    with bot.journal_file.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


async def test_setup_and_a_trade_stopped_out_at_the_exchange(
    venue: FakeTrading, tmp_path: Path
) -> None:
    async with running(venue, tmp_path) as (bot, broker, _lines):
        assert venue.margin_type[SYM] == "ISOLATED" and venue.leverage[SYM] == 10
        assert broker.equity == 10_000.0  # the USDC wallet
        res = await bot.open(long())
        assert "LONG limit" in res, res
        await fill_entry(venue, bot)
        stop = venue.open_algos_of(SYM)[0]
        assert stop.close_position and stop.trigger < Decimal("590")
        tp = [o for o in venue.open_orders_of(SYM) if o.reduce_only]
        assert len(tp) == 1 and tp[0].tif == "GTX" and tp[0].price > Decimal("604")
        venue.set_price(SYM, float(stop.trigger) - 1.0)
        await until(lambda: SYM not in bot.trades)
        row = journal(bot)[0]
        assert row["sebep"] == "zarar kes" and float(row["kar_zarar"]) < 0
        assert not venue.open_orders_of(SYM) and not venue.open_algos_of(SYM)
        assert broker.t.kill.level is Level.ACTIVE, broker.t.kill.reasons


async def test_time_exit_falls_back_to_a_market_order(venue: FakeTrading, tmp_path: Path) -> None:
    timing = Timing(grid_s=3600, hold_s=0.5, entry_s=30, exit_s=0.5, loop_s=0.1)
    async with running(venue, tmp_path, timing) as (bot, _broker, _lines):
        await bot.open(long())
        await fill_entry(venue, bot)
        await until(lambda: SYM not in bot.trades)
        row = journal(bot)[0]
        assert row["sebep"] == "süre (piyasa emri)", row
        assert venue.positions[SYM].amount == 0 and not venue.open_algos_of(SYM)


async def test_maker_fee_above_zero_blocks_new_trades(venue: FakeTrading, tmp_path: Path) -> None:
    venue.maker_fee = "0.000200"
    async with running(venue, tmp_path) as (bot, _broker, _lines):
        await until(lambda: SYM in bot.blocked_symbols)
        res = await bot.open(long())
        assert "maker ücreti" in res and not venue.open_orders_of(SYM)


async def test_multi_assets_mode_refuses_isolated_margin(
    venue: FakeTrading, tmp_path: Path
) -> None:
    venue.multi_assets = True
    cfg = bot_config(tmp_path)
    tcfg = exchange_config(cfg, [SYM]).model_copy(
        update={"rest_url": venue.rest_url, "ws_url": venue.ws_url}
    )
    async with aiohttp.ClientSession() as session:
        broker = ExchangeBroker(tcfg, session, HmacSigner("test-key", "test-secret"))
        with pytest.raises(RuntimeError, match="Multi-Assets"):
            await broker.t.startup()
        broker.t.store.close()


async def test_ctrl_c_closes_and_a_restart_resumes(venue: FakeTrading, tmp_path: Path) -> None:
    # 1) close_on_exit (default): stopping the bot closes its position
    async with running(venue, tmp_path) as (bot, _broker, _lines):
        await bot.open(long())
        await fill_entry(venue, bot)
    assert venue.positions[SYM].amount == 0 and not venue.open_algos_of(SYM)
    assert journal(bot)[-1]["sebep"] == "kapatıldı"
    # 2) close_on_exit off: the position and its stop stay; a restart resumes the trade
    async with running(venue, tmp_path, close_on_exit=False) as (bot, _broker, _lines):
        await bot.open(long(600))
        await fill_entry(venue, bot)
    assert venue.positions[SYM].amount > 0 and venue.open_algos_of(SYM)
    async with running(venue, tmp_path, close_on_exit=False) as (bot, _broker, _lines):
        assert bot.trades[SYM].state == "open"
        await asyncio.sleep(0.5)
        assert SYM in bot.trades and venue.positions[SYM].amount > 0  # not an orphan
        stop = venue.open_algos_of(SYM)[0]
        venue.set_price(SYM, float(stop.trigger) - 1.0)
        await until(lambda: SYM not in bot.trades)
        assert journal(bot)[-1]["sebep"] == "zarar kes"
