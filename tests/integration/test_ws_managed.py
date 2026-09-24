import asyncio
import contextlib
import random
from collections.abc import AsyncIterator

import aiohttp
import pytest
from aiohttp import web

from quanta.core.clock import LiveClock
from quanta.net.ws import LifecycleEvent, ManagedWebSocket, WsSettings


class CounterServer:
    """Broadcasts an increasing counter to every connection every 10 ms."""

    def __init__(self) -> None:
        self.counter = 0
        self.conns: list[web.WebSocketResponse] = []
        self.silent = False
        self.close_after: int | None = None
        app = web.Application()
        app.router.add_get("/ws", self.handler)
        self.app = app
        self.port = 0

    async def handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.conns.append(ws)
        sent = 0
        with contextlib.suppress(Exception):
            async for _ in ws:
                pass
        self.conns.remove(ws)
        _ = sent
        return ws

    async def tick(self) -> None:
        while True:
            await asyncio.sleep(0.01)
            self.counter += 1
            if self.silent:
                continue
            for ws in list(self.conns):
                with contextlib.suppress(Exception):
                    await ws.send_str(str(self.counter))


@pytest.fixture
async def server() -> AsyncIterator[CounterServer]:
    srv = CounterServer()
    runner = web.AppRunner(srv.app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    srv.port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    task = asyncio.create_task(srv.tick())
    yield srv
    task.cancel()
    await runner.cleanup()


def _mk(
    srv: CounterServer,
    session: aiohttp.ClientSession,
    frames: list[tuple[str, int]],
    events: list[LifecycleEvent],
    **kw: float,
) -> ManagedWebSocket:
    settings = WsSettings(**{"backoff_initial_s": 0.05, "backoff_max_s": 0.2, **kw})  # type: ignore[arg-type]
    return ManagedWebSocket(
        "t",
        f"http://127.0.0.1:{srv.port}/ws",
        session,
        LiveClock(),
        on_frame=lambda c, n, ts, text: frames.append((c, int(text))),
        on_lifecycle=events.append,
        settings=settings,
        rng=random.Random(1),
    )


async def test_reconnects_after_server_close(server: CounterServer) -> None:
    frames: list[tuple[str, int]] = []
    events: list[LifecycleEvent] = []
    async with aiohttp.ClientSession() as session:
        m = _mk(server, session, frames, events)
        task = asyncio.create_task(m.run())
        await asyncio.sleep(0.3)
        for ws in list(server.conns):
            await ws.close()
        await asyncio.sleep(0.5)
        m.stop()
        await asyncio.wait_for(task, 5)
    kinds = [e.event for e in events]
    assert kinds.count("connected") >= 2
    assert "disconnected" in kinds and kinds[-1] == "stopped"
    conn_ids = {c for c, _ in frames}
    assert len(conn_ids) >= 2  # frames arrived on the reconnected socket too


async def test_make_before_break_rotation_loses_nothing(server: CounterServer) -> None:
    frames: list[tuple[str, int]] = []
    events: list[LifecycleEvent] = []
    async with aiohttp.ClientSession() as session:
        m = _mk(
            server, session, frames, events, max_age_s=0.4, overlap_s=0.1, first_frame_timeout_s=1.0
        )
        task = asyncio.create_task(m.run())
        await asyncio.sleep(1.6)
        m.stop()
        await asyncio.wait_for(task, 5)
    assert sum(e.event == "rotation_completed" for e in events) >= 2
    values = sorted({v for _, v in frames})
    # union over all connections is contiguous: no counter value was missed at rotations
    assert values == list(range(values[0], values[-1] + 1))
    # overlap produced duplicates, which consumers dedupe by id
    assert len(frames) > len(values)


async def test_idle_timeout_triggers_reconnect(server: CounterServer) -> None:
    frames: list[tuple[str, int]] = []
    events: list[LifecycleEvent] = []
    server.silent = True
    async with aiohttp.ClientSession() as session:
        m = _mk(server, session, frames, events, idle_timeout_s=0.2, heartbeat_s=None)
        task = asyncio.create_task(m.run())
        await asyncio.sleep(0.9)
        m.stop()
        await asyncio.wait_for(task, 5)
    reasons = [e.detail.get("reason") for e in events if e.event == "disconnected"]
    assert "idle_timeout" in reasons
    assert sum(e.event == "connected" for e in events) >= 2


async def test_connect_failure_backs_off_and_stops() -> None:
    events: list[LifecycleEvent] = []
    async with aiohttp.ClientSession() as session:
        m = ManagedWebSocket(
            "t",
            "http://127.0.0.1:9/ws",
            session,
            LiveClock(),
            on_frame=lambda *a: None,
            on_lifecycle=events.append,
            settings=WsSettings(backoff_initial_s=0.05, backoff_max_s=0.1),
            rng=random.Random(1),
        )
        task = asyncio.create_task(m.run())
        await asyncio.sleep(0.5)
        m.stop()
        await asyncio.wait_for(task, 5)
    assert sum(e.event == "connect_failed" for e in events) >= 2
    assert not m.connected
