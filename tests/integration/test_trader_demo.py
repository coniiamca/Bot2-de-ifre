"""The trader end to end against the fake trading venue: start-up in SAFE mode, the canary,
protective stops, the emergency close, persistence of HALTED across restarts, stale data,
dropped streams, orders with an unknown outcome, the daily loss limit, orders placed behind
the trader's back, and the drills."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from quanta.oms import oms as oms_mod
from quanta.oms.oms import Intent
from quanta.oms.state import OrderStatus
from quanta.risk.killswitch import Level
from quanta.risk.limits import RiskLimits
from quanta.trader import service as service_mod
from quanta.trader.canary import canary_cycle
from quanta.trader.config import CanaryConfig, DrillConfig, TraderConfig
from quanta.trader.drills import run_drills
from quanta.trader.service import Trader, write_command
from quanta.venues.binance_usdm.signing import HmacSigner
from tests.integration.fake_trading import FakeTrading

SYM = "BTCUSDT"


@pytest.fixture
async def venue() -> AsyncIterator[FakeTrading]:
    fv = FakeTrading()
    await fv.start()
    yield fv
    await fv.stop()


async def until(pred: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.05)


def config(venue: FakeTrading, state: Path, **limits: Any) -> TraderConfig:
    return TraderConfig(
        rest_url=venue.rest_url,
        ws_url=venue.ws_url,
        state_dir=state,
        canary=CanaryConfig(enabled=False, entry_wait_s=3.0, hold_s=1.0),
        drills=DrillConfig(enabled=False, deadman_countdown_ms=600),
        reconcile_every_s=1.0,
        account_every_s=0.3,
        deadman_renew_s=0.3,
        loop_s=0.1,
        limits=RiskLimits(stale_stream_s=1.0, **limits),
    )


@asynccontextmanager
async def running(venue: FakeTrading, state: Path, **limits: Any) -> AsyncIterator[Trader]:
    async with aiohttp.ClientSession() as session:
        t = Trader(config(venue, state, **limits), session, HmacSigner("test-key", "test-secret"))
        stop = asyncio.Event()
        task = asyncio.create_task(t.run(stop))
        try:
            await until(lambda: t.ready and t.market.view(SYM).mark > 0 and t.user.connected)
            await until(lambda: t.kill.level is Level.ACTIVE or t.kill.sticky > Level.ACTIVE)
            yield t
        finally:
            stop.set()
            await task


async def fill_when_resting(venue: FakeTrading, mid: float) -> None:
    await until(lambda: bool(venue.open_orders_of(SYM)))
    venue.set_price(SYM, mid)


async def test_start_safe_then_active_and_a_canary_cycle(
    venue: FakeTrading, tmp_path: Path
) -> None:
    async with running(venue, tmp_path / "st") as t:
        assert (
            not venue.dual_side
            and venue.margin_type[SYM] == "ISOLATED"
            and venue.leverage[SYM] == 3
        )
        assert t.kill.level is Level.ACTIVE and t.equity == 10_000.0
        mover = asyncio.create_task(fill_when_resting(venue, 64999.8))
        res = await canary_cycle(t, 1)
        await mover
        assert res["result"] == "ok", res
        assert res["stop"] == "ok" and res["fills"] >= 2 and res["ack_ms"] is not None
        assert venue.positions[SYM].amount == 0 and not venue.open_algos_of(SYM)
        assert not t.store.positions()
        await until(lambda: (tmp_path / "st" / "state.json").exists())
        state = json.loads((tmp_path / "st" / "state.json").read_text())
        assert state["level"] == "ACTIVE" and state["environment"] == "demo"
        no_fill = await canary_cycle(t, 2)  # nobody trades through our bid this time
        assert no_fill["result"] == "not_filled" and not venue.open_orders_of(SYM)
        # while the entry order rested, the dead-man switch was armed
        assert any(p == "/fapi/v1/countdownCancelAll" for _, p, _ in venue.requests)


async def test_a_position_gets_a_protective_stop_that_fires(
    venue: FakeTrading, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_mod, "PROTECT_GRACE_NS", 200_000_000)
    async with running(venue, tmp_path / "st") as t:
        r = await t.place(Intent("entry", SYM, "BUY", "MARKET", Decimal("0.002")))
        assert r.ok
        await until(lambda: bool(venue.open_algos_of(SYM)))  # placed by the trader itself
        stop = venue.open_algos_of(SYM)[0]
        assert stop.close_position and stop.trigger < Decimal(65000)
        venue.set_price(SYM, float(stop.trigger) - 50)
        await until(lambda: venue.positions[SYM].amount == 0)
        await until(lambda: not t.store.positions())
        await asyncio.sleep(1.5)  # a reconciliation later: nothing unexpected
        assert t.kill.level is Level.ACTIVE, t.kill.reasons


async def test_emergency_close_halts_and_only_a_person_resumes(
    venue: FakeTrading, tmp_path: Path
) -> None:
    state = tmp_path / "st"
    async with running(venue, state) as t:
        assert (await t.place(Intent("entry", SYM, "BUY", "MARKET", Decimal("0.002")))).ok
        await t.place(
            Intent("entry", SYM, "BUY", "LIMIT", Decimal("0.002"), Decimal("64900"), "GTX")
        )
        write_command(state, "flatten", "test")
        await until(lambda: t.kill.level is Level.HALTED)
        assert venue.positions[SYM].amount == 0
        assert not venue.open_orders_of(SYM) and not venue.open_algos_of(SYM)
        refused = await t.place(Intent("entry", SYM, "BUY", "MARKET", Decimal("0.002")))
        assert refused.status is OrderStatus.REJECTED and "kill switch" in (refused.error or "")
    async with running(venue, state) as t2:  # a restart does not clear HALTED
        await asyncio.sleep(0.5)
        assert t2.kill.level is Level.HALTED
        write_command(state, "resume", "test")
        await until(lambda: t2.kill.level is Level.ACTIVE)


async def test_stale_market_data_and_a_dropped_stream_pause_entries(
    venue: FakeTrading, tmp_path: Path
) -> None:
    async with running(venue, tmp_path / "st") as t:
        venue.streams_paused = True
        await until(lambda: "stale_data" in t.kill.conditions)
        r = await t.place(Intent("entry", SYM, "BUY", "MARKET", Decimal("0.002")))
        assert r.status is OrderStatus.REJECTED
        venue.streams_paused = False
        await until(lambda: t.kill.level is Level.ACTIVE)
        venue.stream_interval_s = 0.2
        await venue.drop_private()
        await until(lambda: t.user.reconnects >= 1 and t.user.connected)
        await until(lambda: t.kill.level is Level.ACTIVE)


async def test_an_unknown_outcome_freezes_the_symbol_until_resolved(
    venue: FakeTrading, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(oms_mod, "UNKNOWN_GRACE_NS", 300_000_000)
    async with running(venue, tmp_path / "st") as t:
        unknown = "Unknown error, please check your request or try again later."
        venue.fault("/fapi/v1/order", 503, None, unknown, execute=True)
        venue.mute_private(True)  # the stream must not tell us before the error does
        r = await t.place(
            Intent("entry", SYM, "BUY", "LIMIT", Decimal("0.002"), Decimal("64900"), "GTX")
        )
        assert r.status is OrderStatus.UNKNOWN and SYM in t.oms.frozen
        blocked = await t.place(
            Intent("entry", SYM, "BUY", "LIMIT", Decimal("0.002"), Decimal("64800"), "GTX")
        )
        assert blocked.status is OrderStatus.REJECTED and "donduruldu" in (blocked.error or "")
        venue.mute_private(False)
        await until(lambda: SYM not in t.oms.frozen)
        assert t.store.order(r.client_id or "").status is OrderStatus.NEW  # type: ignore[union-attr]
        await until(lambda: t.kill.level is Level.ACTIVE)
        assert len(venue.open_orders_of(SYM)) == 1  # never sent twice
        venue.fault("/fapi/v1/order", 503, None, unknown, execute=False)
        await until(lambda: t.kill.level is Level.ACTIVE)
        lost = await t.place(
            Intent("entry", SYM, "BUY", "LIMIT", Decimal("0.002"), Decimal("64800"), "GTX")
        )
        assert lost.status is OrderStatus.UNKNOWN and SYM in t.oms.frozen
        await until(lambda: SYM not in t.oms.frozen)
        assert t.store.order(lost.client_id or "").status is OrderStatus.REJECTED  # type: ignore[union-attr]


async def test_daily_loss_limit_closes_everything_and_halts(
    venue: FakeTrading, tmp_path: Path
) -> None:
    async with running(
        venue, tmp_path / "st", daily_loss_reduce=0.0001, daily_loss_halt=0.0002
    ) as t:
        assert (await t.place(Intent("entry", SYM, "BUY", "MARKET", Decimal("0.002")))).ok
        venue.set_price(SYM, 63_900.0)  # −2.2 USDT on 10 000: past the (test) halt limit
        await until(lambda: t.kill.level is Level.HALTED)
        assert venue.positions[SYM].amount == 0
        assert "günlük zarar" in t.kill.sticky_reason


async def test_orders_placed_behind_the_traders_back_pause_it(
    venue: FakeTrading, tmp_path: Path
) -> None:
    async with running(venue, tmp_path / "st") as t:
        await t.rest.new_order(SYM, "BUY", "LIMIT", "manual-1", "0.002", "64000", "GTX")
        await until(lambda: "external_order" in t.kill.conditions)
        assert not t.kill.allows_entry()
        await t.rest.cancel_order(SYM, "manual-1")
        await until(lambda: t.kill.level is Level.ACTIVE)


async def test_drills(venue: FakeTrading, tmp_path: Path) -> None:
    async with running(venue, tmp_path / "st") as t:
        await run_drills(t)
        assert t.drills["flatten"]["result"] == "ok", t.drills
        assert t.drills["deadman"] == t.drills["deadman"] | {
            "result": "ok",
            "regular_cancelled": True,
            "algo_cancelled": False,
        }
        assert t.drills["reconnect"]["result"] == "ok"
        assert t.kill.level is Level.ACTIVE  # the flatten drill resumed itself (demo only)
        assert t.store.get_json("deadman_cancels_algo") is False


def test_example_config_is_valid_and_demo_only() -> None:
    from quanta.core.config import load_yaml_config

    cfg = load_yaml_config(Path("config/trader-demo.example.yaml"), TraderConfig)
    assert cfg.environment == "demo" and "demo" in cfg.rest_url and cfg.leverage <= 3
    assert cfg.limits.daily_loss_halt == 0.03 and cfg.limits.max_positions == 3
    with pytest.raises(ValueError, match="only on the demo"):
        TraderConfig(rest_url="https://fapi.binance.com")


async def test_cli_check_reports_the_connection(venue: FakeTrading, tmp_path: Path) -> None:
    import os

    from typer.testing import CliRunner

    from quanta.cli import app

    secrets = tmp_path / "secrets"
    secrets.mkdir()
    for name, value in (("api_key", "test-key"), ("secret", "test-secret"), ("bad", "nope")):
        (secrets / name).write_text(value)
        os.chmod(secrets / name, 0o400)
    conf = tmp_path / "trader.yaml"

    def write(secret: str) -> None:
        conf.write_text(
            f"rest_url: {venue.rest_url}\nws_url: {venue.ws_url}\nstate_dir: {tmp_path / 'st'}\n"
            f"keys:\n  type: hmac\n  api_key_file: {secrets / 'api_key'}\n"
            f"  secret_file: {secrets / secret}\n"
        )

    runner = CliRunner()
    write("secret")
    res = await asyncio.to_thread(runner.invoke, app, ["trader", "check", "-c", str(conf)])
    assert res.exit_code == 0, res.output
    out = json.loads(res.output.strip().splitlines()[-1])
    assert out["ok"] and out["balance_usdt"] == "10000" and out["key_type"] == "hmac"
    write("bad")
    res = await asyncio.to_thread(runner.invoke, app, ["trader", "check", "-c", str(conf)])
    assert res.exit_code == 1 and '"ok": false' in res.output and "test-secret" not in res.output
