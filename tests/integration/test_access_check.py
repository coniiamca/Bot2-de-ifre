"""Access check against fake Binance, Bybit and Deribit (reachable, geo-blocked, access.json)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from quanta.recorder.config import RecorderConfig
from quanta.tools.access import (
    VENUES,
    access_document,
    binance_probe,
    bybit_probe,
    check_all,
    check_venue,
    deribit_probe,
    probes_from_config,
    report_dict,
    write_access,
)
from quanta.ui.health import HealthConfig, evaluate
from quanta.ui.history import History
from quanta.ui.sources import load_access
from tests.integration.fake_binance import FakeBinance
from tests.integration.fake_venues import FakeBybit, FakeDeribit


@pytest.fixture
async def fake() -> AsyncIterator[FakeBinance]:
    f = FakeBinance(["BTCUSDT"])
    await f.start()
    yield f
    await f.stop()


@pytest.fixture
async def bybit() -> AsyncIterator[FakeBybit]:
    f = FakeBybit(["BTCUSDT"])
    await f.start()
    yield f
    await f.stop()


@pytest.fixture
async def deribit() -> AsyncIterator[FakeDeribit]:
    f = FakeDeribit(["BTC-PERPETUAL"])
    await f.start()
    yield f
    await f.stop()


async def test_binance_ok(fake: FakeBinance) -> None:
    probe = binance_probe(rest_url=fake.rest_url, ws_url=fake.ws_url)
    rep = await check_venue(probe, samples=3, ws_seconds=0.5)
    assert rep.ok, report_dict(rep)
    assert rep.ws_messages["market"] > 0 and rep.ws_messages["public"] > 0
    assert abs(rep.clock_offset_ms or 0) < 1000
    assert rep.ws_latency_ms["market"]["n"] > 0


async def test_binance_restricted(fake: FakeBinance) -> None:
    fake.restricted = True
    rep = await check_venue(binance_probe(rest_url=fake.rest_url, ws_url=fake.ws_url), 2, 0.2)
    assert rep.restricted and not rep.ok
    assert rep.http_status == 451 and "451" in (rep.error or "")


async def test_bybit_and_deribit_ok(bybit: FakeBybit, deribit: FakeDeribit) -> None:
    reports = await check_all(
        [
            bybit_probe(rest_url=bybit.rest_url, ws_url=bybit.ws_url),
            deribit_probe(rest_url=deribit.rest_url, ws_url=deribit.ws_url),
        ],
        samples=3,
        ws_seconds=0.5,
    )
    for rep in reports:
        assert rep.ok, report_dict(rep)
        assert rep.rtt_ms["n"] == 3 and abs(rep.clock_offset_ms or 0) < 1000
        (count,) = rep.ws_messages.values()
        (lat,) = rep.ws_latency_ms.values()
        assert count > 0 and lat["n"] > 0


async def test_geo_block_on_rest(bybit: FakeBybit) -> None:
    bybit.geo_blocked = True
    rep = await check_venue(bybit_probe(rest_url=bybit.rest_url, ws_url=bybit.ws_url), 2, 0.2)
    assert rep.restricted and rep.http_status == 403 and not rep.reachable
    assert bybit.ws_connects == 0  # no point in trying the WebSocket


async def test_geo_block_on_ws_handshake_only(deribit: FakeDeribit) -> None:
    probe = deribit_probe(rest_url=deribit.rest_url, ws_url=deribit.ws_url)
    deribit.ws_blocked = True
    rep = await check_venue(probe, 1, 0.2)
    assert rep.reachable and rep.restricted and not rep.ok
    assert rep.http_status == 403 and (rep.error or "").startswith("ws main")


async def test_ws_not_found_is_an_error_not_a_block(deribit: FakeDeribit) -> None:
    wrong = deribit_probe(
        rest_url=deribit.rest_url, ws_url=deribit.ws_url.replace("/ws/", "/nope/")
    )
    rep = await check_venue(wrong, 1, 0.2)
    assert rep.reachable and not rep.restricted and not rep.ok
    assert "ws main" in (rep.error or "")


async def test_subscribe_rejection_is_an_error(bybit: FakeBybit) -> None:
    probe = bybit_probe(symbol="NOPE", rest_url=bybit.rest_url, ws_url=bybit.ws_url)
    rep = await check_venue(probe, 1, 0.3)
    # the fake accepts any topic but never publishes NOPE → "no market data"
    assert not rep.ok and "no market data" in (rep.error or "")


async def test_access_json_feeds_status_page(
    tmp_path: Path, fake: FakeBinance, bybit: FakeBybit, deribit: FakeDeribit
) -> None:
    bybit.geo_blocked = True
    cfg = RecorderConfig.model_validate(
        {
            "data_dir": str(tmp_path),
            "binance_usdm": {"rest_url": fake.rest_url, "ws_url": fake.ws_url},
            "bybit_linear": {
                "enabled": True,
                "rest_url": bybit.rest_url,
                "ws_url": bybit.ws_url,
            },
            "deribit": {"enabled": True, "rest_url": deribit.rest_url, "ws_url": deribit.ws_url},
        }
    )
    probes = probes_from_config(cfg)
    assert [p.venue for p in probes] == ["binance_usdm", "bybit_linear", "deribit"]
    reports = await check_all(probes, samples=2, ws_seconds=0.3)
    doc = access_document(reports, datetime(2026, 9, 24, 12, tzinfo=UTC))
    path = tmp_path / "access.json"
    write_access(path, doc)

    loaded = load_access(path)
    assert loaded is not None and loaded["checked_at"] == "2026-09-24T12:00:00Z"
    assert loaded["ok"] is False
    v = loaded["venues"]
    assert v["binance_usdm"]["ok"] and v["deribit"]["ok"]
    assert isinstance(v["binance_usdm"]["rtt_ms"], float)
    assert v["bybit_linear"]["restricted"] and v["bybit_linear"]["http_status"] == 403
    json.dumps(loaded)  # plain JSON

    verdict = evaluate(History(), 0.0, 0.0, HealthConfig(), access=loaded)
    access = [i for i in verdict.issues if i.code == "access"]
    assert [(i.level, "Bybit" in i.title) for i in access] == [("critical", True)]


def test_probes_default_and_filter() -> None:
    assert [p.venue for p in probes_from_config(None)] == list(VENUES)
    assert [p.venue for p in probes_from_config(None, ["deribit"])] == ["deribit"]
    cfg = RecorderConfig.model_validate({"data_dir": "."})  # Bybit/Deribit disabled by default
    assert [p.venue for p in probes_from_config(cfg)] == ["binance_usdm"]
