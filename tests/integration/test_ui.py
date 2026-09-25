"""Status UI against a live recorder (fake exchange) through the real /metrics endpoint."""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from quanta.core.clock import LiveClock
from quanta.recorder.config import RecorderConfig
from quanta.recorder.service import RecorderService
from quanta.ui.app import UiConfig, build_app
from quanta.ui.health import HealthConfig
from tests.integration.fake_binance import FakeBinance


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def wait_for(pred: Any, timeout: float = 8.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not await pred():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.1)


async def test_ui_reports_ok_then_problem(tmp_path: Path) -> None:
    fake = FakeBinance(["BTCUSDT"])
    await fake.start()
    port = free_port()
    cfg = RecorderConfig.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "min_free_disk_gb": 0,
            "binance_usdm": {
                "depth_symbols": ["BTCUSDT"],
                "universe": ["BTCUSDT"],
                "rest_url": fake.rest_url,
                "ws_url": fake.ws_url,
            },
            "segments": {"flush_interval_s": 0.2, "fsync": False},
            "ws": {"backoff_max_s": 0.2, "stable_after_s": 0.5},
            "metrics": {"enabled": True, "host": "127.0.0.1", "port": port},
        }
    )
    svc = RecorderService(cfg, LiveClock())
    stop = asyncio.Event()
    rec = asyncio.create_task(svc.run(stop))
    ui_cfg = UiConfig(
        metrics_url=f"http://127.0.0.1:{port}/metrics",
        data_dir=cfg.data_dir,
        scrape_interval_s=0.2,
        health=HealthConfig(stream_down_s=1.0, book_unsynced_s=1.0, stream_silent_s=2.0),
    )
    client = TestClient(TestServer(build_app(ui_cfg)))
    await client.start_server()
    try:
        page = await client.get("/")
        assert page.status == 200 and "Kayıt durumu" in await page.text()

        async def status() -> dict[str, Any]:
            r = await client.get("/api/status")
            assert r.status == 200
            data: dict[str, Any] = await r.json()
            return data

        async def healthy() -> bool:
            d = await status()
            return (
                d["verdict"]["level"] == "ok"
                and bool(d["venues"])
                and all(b["synced"] for b in d["venues"][0]["books"])
                and d["venues"][0]["msg_rate"] is not None
            )

        await wait_for(healthy)
        d = await status()
        v = d["venues"][0]
        assert v["name"] == "Binance USDⓈ-M"
        assert all(s["up"] for s in v["streams"]) and v["msg_rate"] is not None
        assert d["system"]["disk_free_gb"] is not None
        # Phase 0 section: nothing checked yet, then a check file appears
        assert d["checks"] == [] and d["phase0"] == {"full_days": 0, "target_days": 3}
        assert d["system"]["disk_projection"] is None  # no finished day yet
        cdir = cfg.data_dir / "lake" / "_checks"
        cdir.mkdir(parents=True)
        (cdir / "date=2026-09-26.json").write_text(
            '{"date": "2026-09-26", "status": "ok", "checked_at": "2026-09-27T06:20:00Z",'
            ' "trades": {"BTCUSDT": {"status": "ok"}}, "book": {"BTCUSDT": {"status": "ok",'
            ' "compared": 144}}}'
        )
        d = await status()
        assert d["checks"][0]["trades_state"] == "ok" and d["checks"][0]["book_state"] == "ok"

        # the exchange goes away: every stream disconnects and reconnects fail
        fake.restricted = True
        await fake.close_all_ws()

        async def broken() -> bool:
            d = await status()
            return d["verdict"]["level"] == "critical" and any(
                i["code"] in ("stream_down", "restricted") for i in d["verdict"]["issues"]
            )

        await wait_for(broken)
        d = await status()
        assert d["verdict"]["title"] == "Sorun var"
        assert all(i["runbook"] for i in d["verdict"]["issues"])
    finally:
        await client.close()
        stop.set()
        await asyncio.wait_for(rec, 10)
        await fake.stop()
