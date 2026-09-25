"""Signed trading client and user stream against the fake trading venue."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from decimal import Decimal

import aiohttp
import pytest

from quanta.core.clock import LiveClock
from quanta.venues.binance_usdm.errors import Action
from quanta.venues.binance_usdm.filters import parse_rules
from quanta.venues.binance_usdm.ratelimit import WeightBudget
from quanta.venues.binance_usdm.signing import HmacSigner
from quanta.venues.binance_usdm.trade_rest import BinanceTradeRest, TradeError
from quanta.venues.binance_usdm.user_stream import (
    AccountUpdate,
    AlgoUpdate,
    OrderUpdate,
    UserEvent,
    UserStream,
)
from tests.integration.fake_trading import FakeTrading


@pytest.fixture
async def venue() -> AsyncIterator[FakeTrading]:
    fv = FakeTrading()
    await fv.start()
    yield fv
    await fv.stop()


@pytest.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as s:
        yield s


def client(
    venue: FakeTrading, session: aiohttp.ClientSession, secret: str = "test-secret"
) -> BinanceTradeRest:
    clock = LiveClock()
    return BinanceTradeRest(
        session,
        venue.rest_url,
        HmacSigner("test-key", secret),
        WeightBudget(clock),
        clock,
        timeout_s=2.0,
    )


async def until(pred: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


async def test_account_setup_and_rules(venue: FakeTrading, session: aiohttp.ClientSession) -> None:
    rest = client(venue, session)
    offset = await rest.sync_time()
    assert abs(offset) < 1.0
    rules = parse_rules(await rest.exchange_info())["BTCUSDT"]
    assert rules.min_notional == Decimal(100) and rules.tick == Decimal("0.10")
    await rest.set_one_way()
    await rest.set_one_way()  # already one-way: accepted silently
    await rest.set_isolated("BTCUSDT")
    await rest.set_isolated("BTCUSDT")
    await rest.set_leverage("BTCUSDT", 3)
    assert not venue.dual_side and venue.margin_type["BTCUSDT"] == "ISOLATED"
    assert venue.leverage["BTCUSDT"] == 3


async def test_order_lifecycle_with_protective_stop(
    venue: FakeTrading, session: aiohttp.ClientSession
) -> None:
    rest = client(venue, session)
    events: list[UserEvent] = []
    stop = asyncio.Event()
    stream = UserStream(rest, venue.ws_url, session, LiveClock(), events.append)
    task = asyncio.create_task(stream.run(stop))
    await until(lambda: stream.connected)

    await rest.new_order("BTCUSDT", "BUY", "LIMIT", "t-1-1", "0.002", "64990", "GTX")
    await until(lambda: any(isinstance(e, OrderUpdate) and e.status == "NEW" for e in events))
    venue.set_price("BTCUSDT", 64980.0)  # the ask trades through our bid
    await until(lambda: any(isinstance(e, OrderUpdate) and e.status == "FILLED" for e in events))
    fill = next(e for e in events if isinstance(e, OrderUpdate) and e.exec_type == "TRADE")
    assert fill.last_qty == Decimal("0.002") and fill.last_price == Decimal(64990)
    assert fill.fee > 0 and fill.trade_id > 0
    await until(lambda: any(isinstance(e, AccountUpdate) for e in events))
    pos = await rest.position_risk("BTCUSDT")
    assert Decimal(pos[0]["positionAmt"]) == Decimal("0.002")

    await rest.new_stop_close("BTCUSDT", "SELL", "64000", "t-2-1")
    await until(lambda: any(isinstance(e, AlgoUpdate) and e.status == "NEW" for e in events))
    assert len(await rest.open_algo_orders("BTCUSDT")) == 1
    venue.set_price("BTCUSDT", 63900.0)  # mark below the trigger
    await until(lambda: any(isinstance(e, AlgoUpdate) and e.status == "FINISHED" for e in events))
    await until(lambda: venue.positions["BTCUSDT"].amount == 0)
    assert await rest.position_risk("BTCUSDT") == []
    account = await rest.account()
    assert Decimal(account["totalWalletBalance"]) < Decimal(10000)  # loss + fees
    stop.set()
    await task


async def test_errors_are_classified(venue: FakeTrading, session: aiohttp.ClientSession) -> None:
    rest = client(venue, session)
    with pytest.raises(TradeError) as e:
        await rest.new_order("BTCUSDT", "BUY", "LIMIT", "x-1-1", "0.002", "65100", "GTX")
    assert e.value.action is Action.REJECTED and e.value.code == -5022
    with pytest.raises(TradeError) as e:
        await rest.new_order("BTCUSDT", "BUY", "STOP_MARKET", "x-2-1", "0.002")
    assert e.value.code == -4120
    with pytest.raises(TradeError) as e:
        await rest.new_order("BTCUSDT", "SELL", "MARKET", "x-3-1", "0.002", reduce_only=True)
    assert e.value.code == -2022  # nothing to reduce
    # the order is placed, but the answer is "unknown": it must be found by its id
    venue.fault(
        "/fapi/v1/order",
        503,
        None,
        "Unknown error, please check your request or try again later.",
        execute=True,
    )
    with pytest.raises(TradeError) as e:
        await rest.new_order("BTCUSDT", "BUY", "LIMIT", "x-4-1", "0.002", "64000", "GTX")
    assert e.value.action is Action.UNKNOWN
    assert (await rest.query_order("BTCUSDT", "x-4-1"))["status"] == "NEW"
    with pytest.raises(TradeError) as e:
        await rest.query_order("BTCUSDT", "never-sent")
    assert e.value.action is Action.NOT_FOUND
    venue.stalls["/fapi/v1/order"] = 3.0  # no answer within the client timeout
    with pytest.raises(TradeError) as e:
        await rest.new_order("BTCUSDT", "BUY", "LIMIT", "x-5-1", "0.002", "64000", "GTX")
    assert e.value.action is Action.UNKNOWN and e.value.status == 0
    with pytest.raises(TradeError) as e:
        await client(venue, session, secret="wrong").account()
    assert e.value.action is Action.AUTH


async def test_dead_man_switch_cancels_orders(
    venue: FakeTrading, session: aiohttp.ClientSession
) -> None:
    rest = client(venue, session)
    await rest.new_order("BTCUSDT", "BUY", "LIMIT", "d-1-1", "0.002", "60000", "GTX")
    await rest.new_stop_order("BTCUSDT", "BUY", "90000", "0.002", "d-2-1")
    await rest.countdown_cancel_all("BTCUSDT", 300)
    await until(lambda: not venue.open_orders_of("BTCUSDT"), timeout=3.0)
    assert len(venue.open_algos_of("BTCUSDT")) == 1  # this venue's countdown spares algo orders
    await rest.cancel_all_algo_orders("BTCUSDT")
    assert not venue.open_algos_of("BTCUSDT")


async def test_user_stream_survives_drops_and_key_expiry(
    venue: FakeTrading, session: aiohttp.ClientSession
) -> None:
    rest = client(venue, session)
    events: list[UserEvent] = []
    reconnected: list[int] = []
    stop = asyncio.Event()
    stream = UserStream(
        rest, venue.ws_url, session, LiveClock(), events.append, lambda: reconnected.append(1)
    )
    task = asyncio.create_task(stream.run(stop))
    await until(lambda: stream.connected)
    await venue.drop_private()
    await until(lambda: bool(reconnected) and stream.connected, timeout=10.0)
    venue.expire_listen_keys()
    await until(lambda: bool(venue.listen_keys) and stream.connected, timeout=10.0)
    await rest.new_order("BTCUSDT", "BUY", "LIMIT", "u-1-1", "0.002", "64000", "GTX")
    await until(lambda: any(isinstance(e, OrderUpdate) and e.client_id == "u-1-1" for e in events))
    stop.set()
    await task
