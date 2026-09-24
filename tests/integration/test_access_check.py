from collections.abc import AsyncIterator

import pytest

from quanta.tools.access import check_access, report_dict
from tests.integration.fake_binance import FakeBinance


@pytest.fixture
async def fake() -> AsyncIterator[FakeBinance]:
    f = FakeBinance(["BTCUSDT"])
    await f.start()
    yield f
    await f.stop()


async def test_access_ok(fake: FakeBinance) -> None:
    rep = await check_access(samples=3, ws_seconds=0.5, rest_url=fake.rest_url, ws_url=fake.ws_url)
    assert rep.ok, report_dict(rep)
    assert rep.ws_messages["market"] > 0 and rep.ws_messages["public"] > 0
    assert abs(rep.clock_offset_ms or 0) < 1000


async def test_access_restricted(fake: FakeBinance) -> None:
    fake.restricted = True
    rep = await check_access(samples=2, ws_seconds=0.2, rest_url=fake.rest_url, ws_url=fake.ws_url)
    assert rep.restricted and not rep.ok
    assert "451" in (rep.error or "")
