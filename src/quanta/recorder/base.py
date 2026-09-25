"""Venue-independent capture plumbing shared by every venue adapter.

A venue adapter only implements protocol specifics (subscriptions, sequencing, parsing).
Everything that guarantees data integrity is here and identical for all venues:
raw-first writing, invalid-frame preservation, latency/throughput metrics, lifecycle events
recorded in-band, and a polling loop that records every REST response.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

import aiohttp
import msgspec

from quanta.core.clock import NS_PER_MS, NS_PER_S, Clock
from quanta.core.log import get_logger
from quanta.net.ws import FrameHandler, LifecycleEvent, ManagedWebSocket, OpenHandler, WsSettings
from quanta.recorder.diskguard import DiskGuard
from quanta.recorder.metrics import RecorderMetrics
from quanta.recorder.records import meta_record, rest_record, ws_invalid_record, ws_record
from quanta.recorder.segment import SegmentWriter

log = get_logger(__name__)


class Writers:
    """Segment writers per channel for one venue."""

    def __init__(
        self,
        venue: str,
        writers: dict[str, SegmentWriter],
        metrics: RecorderMetrics,
        channels: tuple[str, ...],
        guard: DiskGuard | None = None,
    ) -> None:
        missing = set(channels) - set(writers)
        if missing:
            raise ValueError(f"missing writers for channels {sorted(missing)}")
        self.venue = venue
        self._w = writers
        self._metrics = metrics
        self._guard = guard
        self.guard_dropped = 0  # records dropped during the current disk-guard activation

    def write(self, channel: str, ts_ns: int, line: bytes) -> None:
        if self._guard is not None and self._guard.active and channel != "meta":
            self.guard_dropped += 1
            self._metrics.disk_guard_dropped.labels(self.venue, channel).inc()
            return
        self._w[channel].append(ts_ns, line)
        self._metrics.bytes.labels(self.venue, channel).inc(len(line) + 1)

    def meta(self, ts_ns: int, type_: str, **fields: Any) -> None:
        self.write("meta", ts_ns, meta_record(ts_ns, type_, venue=self.venue, **fields))
        self._metrics.meta_events.labels(type_).inc()


def chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


class VenueCapture:
    VENUE: ClassVar[str]
    CHANNELS: ClassVar[tuple[str, ...]]

    def __init__(
        self,
        session: aiohttp.ClientSession,
        clock: Clock,
        writers: Writers,
        metrics: RecorderMetrics,
        ws_settings: WsSettings,
        rng: random.Random | None = None,
    ) -> None:
        self._session = session
        self._clock = clock
        self._w = writers
        self._m = metrics
        self._ws_settings = ws_settings
        self._rng = rng or random.Random()  # noqa: S311 — scheduling jitter
        self.streams: list[ManagedWebSocket] = []
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    # -- lifecycle (template) --------------------------------------------------------------
    async def prepare(self) -> None:
        """Fetch metadata and build streams (``add_stream``) before connecting."""

    def pollers(self) -> list[tuple[str, float, Callable[[], Awaitable[None]]]]:
        return []

    async def start(self) -> None:
        self._w.meta(self._clock.now_ns(), "capture_start", **self.describe())
        await self.prepare()
        for s in self.streams:
            self._tasks.append(asyncio.create_task(s.run(), name=f"ws:{s.name}"))
        for name, interval, fn in self.pollers():
            if interval > 0:
                self._tasks.append(
                    asyncio.create_task(self.poll_loop(name, interval, fn), name=f"poll:{name}")
                )

    async def stop(self) -> None:
        self._stop.set()
        for s in self.streams:
            s.stop()
        for t in self._tasks:
            if not t.get_name().startswith("ws:"):
                t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._w.meta(self._clock.now_ns(), "capture_stop")

    def describe(self) -> dict[str, Any]:
        return {}

    def spawn(self, coro: Awaitable[None], name: str) -> None:
        self._tasks.append(asyncio.create_task(coro, name=name))  # type: ignore[arg-type]

    # -- streams ---------------------------------------------------------------------------
    def add_stream(
        self,
        name: str,
        url: str,
        on_frame: FrameHandler,
        on_open: OpenHandler | None = None,
        settings: WsSettings | None = None,
    ) -> ManagedWebSocket:
        ws = ManagedWebSocket(
            name=f"{self.VENUE}:{name}",
            url=url,
            session=self._session,
            clock=self._clock,
            on_frame=on_frame,
            on_lifecycle=self._on_lifecycle,
            settings=settings or self._ws_settings,
            on_open=on_open,
        )
        self.streams.append(ws)
        self._m.connection_up.labels(self.VENUE, ws.name).set(0)
        return ws

    def stream_by_conn(self, conn_id: str) -> ManagedWebSocket | None:
        name = conn_id.split("#", 1)[0]
        return next((s for s in self.streams if s.name == name), None)

    def write_ws(
        self, channel: str, conn_id: str, seq: int, recv_ns: int, text: str
    ) -> bytes | None:
        """Write a frame raw-first. Returns the payload if it is valid JSON, else stores it as
        ``ws_invalid`` (never dropped) and returns None."""
        payload = text.encode()
        try:
            msgspec.json.decode(payload)
        except msgspec.DecodeError:
            self._w.write(channel, recv_ns, ws_invalid_record(recv_ns, conn_id, seq, text))
            self._m.parse_errors.labels(self.VENUE, "invalid_json").inc()
            return None
        self._w.write(channel, recv_ns, ws_record(recv_ns, conn_id, seq, payload))
        return payload

    def observe(self, kind: str, stream: str, recv_ns: int, event_ms: int | None) -> None:
        self._m.messages.labels(self.VENUE, kind).inc()
        if event_ms:
            self._m.event_latency.labels(self.VENUE, kind).observe(
                max(0.0, (recv_ns - event_ms * NS_PER_MS) / NS_PER_S)
            )
        self._m.last_message_ts.labels(self.VENUE, stream).set(recv_ns / NS_PER_S)

    def _on_lifecycle(self, ev: LifecycleEvent) -> None:
        self._w.meta(
            ev.ts_ns,
            "ws_lifecycle",
            stream=ev.name,
            conn_id=ev.conn_id,
            event=ev.event,
            detail=ev.detail,
        )
        self._m.ws_events.labels(self.VENUE, ev.name, ev.event).inc()
        ws = next((s for s in self.streams if s.name == ev.name), None)
        if ws is None:
            return
        if ev.event == "connected":
            self._m.connection_up.labels(self.VENUE, ws.name).set(1)
        elif ev.event in ("disconnected", "connect_failed", "stopped"):
            self._m.connection_up.labels(self.VENUE, ws.name).set(1 if ws.connected else 0)
        self.after_lifecycle(ev, ws)

    def after_lifecycle(self, ev: LifecycleEvent, ws: ManagedWebSocket) -> None:
        """Venue hook (e.g. invalidate books when their connection is lost)."""

    # -- REST --------------------------------------------------------------------------------
    async def http_get(
        self, url: str, params: dict[str, Any], purpose: str, timeout_s: float = 10.0
    ) -> tuple[int, bytes]:
        """Plain GET for venues without weight accounting; the response is always recorded."""
        sent = self._clock.now_ns()
        async with self._session.get(
            url, params=params, timeout=aiohttp.ClientTimeout(total=timeout_s)
        ) as resp:
            body = await resp.read()
            status = resp.status
        recv = self._clock.now_ns()
        path = url.split("://", 1)[-1].split("/", 1)[-1]
        self._w.write(
            "rest",
            recv,
            rest_record(
                recv,
                path="/" + path,
                params=params,
                status=status,
                sent_ns=sent,
                body=body,
                purpose=purpose,
            ),
        )
        endpoint = path.rsplit("/", 1)[-1]
        self._m.rest_requests.labels(self.VENUE, endpoint, str(status)).inc()
        self._m.rest_latency.labels(self.VENUE, endpoint).observe((recv - sent) / NS_PER_S)
        return status, body

    def poll_error(self, name: str, exc: Exception, interval_s: float) -> float:
        """Handle a poller exception; returns the delay before the next attempt."""
        log.warning("poll_failed", venue=self.VENUE, poller=name, error=repr(exc))
        return interval_s

    async def poll_loop(
        self, name: str, interval_s: float, fn: Callable[[], Awaitable[None]]
    ) -> None:
        await asyncio.sleep(self._rng.uniform(0, min(interval_s, 10.0)))
        while not self._stop.is_set():
            delay = interval_s
            try:
                await fn()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                delay = self.poll_error(name, exc, interval_s)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except TimeoutError:
                continue
