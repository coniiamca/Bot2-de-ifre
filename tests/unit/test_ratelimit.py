import asyncio

import pytest

from quanta.core.clock import NS_PER_S, SimClock
from quanta.venues.binance_usdm.ratelimit import WeightBudget


async def test_acquire_within_budget_and_header_update() -> None:
    clock = SimClock(1_000 * NS_PER_S)
    b = WeightBudget(clock, limit_per_minute=100, fraction=0.5)
    assert b.limit == 50
    await b.acquire(20)
    b.release(20, {"X-MBX-USED-WEIGHT-1M": "30"})
    assert b.used == 30
    await b.acquire(20)
    b.release(20, None)
    assert b.used == 50


async def test_blocks_until_next_window() -> None:
    clock = SimClock(60 * NS_PER_S * 10 + 59 * NS_PER_S)  # 1 s before a window boundary
    b = WeightBudget(clock, limit_per_minute=100, fraction=0.5)
    await b.acquire(50)
    b.release(50, None)
    task = asyncio.create_task(b.acquire(10))
    await asyncio.sleep(0.01)
    assert not task.done()
    clock.advance_to(60 * NS_PER_S * 11 + 1)  # next window
    await asyncio.wait_for(task, 2)


async def test_weight_above_budget_rejected() -> None:
    b = WeightBudget(SimClock(0), limit_per_minute=100, fraction=0.1)
    with pytest.raises(ValueError):
        await b.acquire(11)


def test_block_for_sets_deadline() -> None:
    clock = SimClock(5 * NS_PER_S)
    b = WeightBudget(clock)
    b.block_for(3)
    assert b.blocked_until_ns == 8 * NS_PER_S
