"""Managed WebSocket subscription: one logical stream kept alive across failures.

Guarantees and behaviours (plan §8.2, §12.4):

* **Arrival timestamps** are taken immediately on receipt (``clock.now_ns()``) and passed
  with every frame together with ``(conn_id, conn_seq)`` so ordering is reproducible.
* **Reconnect** with exponential backoff + full jitter; backoff resets after a connection
  has been stable for ``stable_after_s``.
* **Stale detection**: idle timeout (no data frame for ``idle_timeout_s``) and protocol
  heartbeats (ping every ``heartbeat_s``; missing pong closes the socket).
* **Make-before-break rotation** before the server-side connection lifetime (24h on
  Binance): a new connection is opened, both deliver frames during an overlap window
  (consumers deduplicate by exchange ids), then the old one is closed. If the new
  connection cannot be opened the old one is kept and rotation is retried.
* Every state change is reported as a ``LifecycleEvent`` so gaps are explicit in the data.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from quanta.core.clock import NS_PER_S, Clock
from quanta.core.log import get_logger

log = get_logger(__name__)

FrameHandler = Callable[[str, int, int, str], None]  # conn_id, conn_seq, recv_ts_ns, text


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    ts_ns: int
    name: str
    conn_id: str | None
    event: str  # connecting|connected|connect_failed|disconnected|rotation_*|stopped
    detail: dict[str, Any] = field(default_factory=dict)


LifecycleHandler = Callable[[LifecycleEvent], None]


@dataclass(frozen=True, slots=True)
class WsSettings:
    max_age_s: float = 23 * 3600.0  # Binance closes connections after 24h
    overlap_s: float = 5.0
    first_frame_timeout_s: float = 15.0
    rotation_retry_s: float = 60.0
    idle_timeout_s: float = 60.0
    heartbeat_s: float | None = 20.0
    connect_timeout_s: float = 10.0
    backoff_initial_s: float = 0.5
    backoff_max_s: float = 30.0
    stable_after_s: float = 60.0
    max_msg_size: int = 16 * 1024 * 1024


class _Physical:
    __slots__ = (
        "closed",
        "conn_id",
        "first_frame",
        "frames",
        "opened_mono_ns",
        "rotate_at_ns",
        "ws",
    )

    def __init__(
        self, conn_id: str, ws: aiohttp.ClientWebSocketResponse, opened: int, rotate_at: int
    ) -> None:
        self.conn_id = conn_id
        self.ws = ws
        self.opened_mono_ns = opened
        self.rotate_at_ns = rotate_at
        self.frames = 0
        self.first_frame = asyncio.Event()
        self.closed = asyncio.Event()


class ManagedWebSocket:
    _instance_counter = itertools.count(1)

    def __init__(
        self,
        name: str,
        url: str,
        session: aiohttp.ClientSession,
        clock: Clock,
        on_frame: FrameHandler,
        on_lifecycle: LifecycleHandler | None = None,
        settings: WsSettings | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.name = name
        self.url = url
        self._session = session
        self._clock = clock
        self._on_frame = on_frame
        self._on_lifecycle = on_lifecycle
        self.settings = settings or WsSettings()
        self._rng = rng or random.Random()  # noqa: S311 — jitter, not cryptography
        self._stop = asyncio.Event()
        self._conn_counter = itertools.count(1)
        self._instance = next(self._instance_counter)
        self._readers: dict[str, asyncio.Task[None]] = {}
        self._active: _Physical | None = None
        self.frame_handler_errors = 0

    # -- public ------------------------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._active is not None and not self._active.closed.is_set()

    @property
    def active_conn_id(self) -> str | None:
        return self._active.conn_id if self._active else None

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        failures = 0  # consecutive failed connects / short-lived connections
        try:
            while not self._stop.is_set():
                if self._active is None or self._active.closed.is_set():
                    prev, self._active = self._active, None
                    if prev is not None:
                        lived_s = (self._clock.monotonic_ns() - prev.opened_mono_ns) / NS_PER_S
                        # A long-lived connection that drops is reconnected immediately.
                        failures = 0 if lived_s >= self.settings.stable_after_s else failures + 1
                    if failures and await self._sleep_or_stop(self._backoff(failures)):
                        break
                    phys = await self._open()
                    if phys is None:
                        failures += 1
                        continue
                    self._active = phys
                    self._start_reader(phys)
                    continue

                phys = self._active
                wait_s = max(0.0, (phys.rotate_at_ns - self._clock.monotonic_ns()) / NS_PER_S)
                closed_task = asyncio.create_task(phys.closed.wait())
                stop_task = asyncio.create_task(self._stop.wait())
                _, pending = await asyncio.wait(
                    {closed_task, stop_task}, timeout=wait_s, return_when=asyncio.FIRST_COMPLETED
                )
                for t in pending:
                    t.cancel()
                if self._stop.is_set() or phys.closed.is_set():
                    continue
                await self._rotate(phys)
        finally:
            await self._shutdown()

    # -- internals ---------------------------------------------------------------------
    def _backoff(self, attempt: int) -> float:
        cap = min(self.settings.backoff_max_s, self.settings.backoff_initial_s * 2 ** (attempt - 1))
        return self._rng.uniform(0, cap)

    async def _sleep_or_stop(self, seconds: float) -> bool:
        """Sleep; returns True if stop was requested meanwhile."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
            return True
        except TimeoutError:
            return False

    def _emit(self, event: str, conn_id: str | None, **detail: Any) -> None:
        ev = LifecycleEvent(self._clock.now_ns(), self.name, conn_id, event, detail)
        log.info("ws_lifecycle", stream=self.name, conn_id=conn_id, ws_event=event, **detail)
        if self._on_lifecycle is not None:
            try:
                self._on_lifecycle(ev)
            except Exception:
                log.exception("lifecycle_handler_failed", stream=self.name)

    async def _open(self) -> _Physical | None:
        conn_id = f"{self.name}#{self._instance}.{next(self._conn_counter)}"
        self._emit("connecting", conn_id, url=self.url)
        s = self.settings
        try:
            ws = await self._session.ws_connect(
                self.url,
                heartbeat=s.heartbeat_s,
                timeout=aiohttp.ClientWSTimeout(ws_receive=None, ws_close=5.0),
                max_msg_size=s.max_msg_size,
                autoping=True,
            )
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            self._emit("connect_failed", conn_id, error=repr(exc))
            return None
        now = self._clock.monotonic_ns()
        phys = _Physical(conn_id, ws, now, now + int(s.max_age_s * NS_PER_S))
        self._emit("connected", conn_id)
        return phys

    def _start_reader(self, phys: _Physical) -> None:
        self._readers[phys.conn_id] = asyncio.create_task(
            self._read(phys), name=f"ws-read:{phys.conn_id}"
        )

    async def _read(self, phys: _Physical) -> None:
        reason = "unknown"
        ws = phys.ws
        try:
            while True:
                msg = await ws.receive(timeout=self.settings.idle_timeout_s)
                if msg.type is aiohttp.WSMsgType.TEXT:
                    text: str = msg.data
                elif msg.type is aiohttp.WSMsgType.BINARY:
                    text = bytes(msg.data).decode("utf-8", "replace")
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                ):
                    reason = f"closed:{ws.close_code}"
                    break
                elif msg.type is aiohttp.WSMsgType.ERROR:
                    reason = f"error:{ws.exception()!r}"
                    break
                else:
                    continue
                recv_ns = self._clock.now_ns()
                phys.frames += 1
                phys.first_frame.set()
                try:
                    self._on_frame(phys.conn_id, phys.frames, recv_ns, text)
                except Exception:
                    self.frame_handler_errors += 1
                    log.exception("frame_handler_failed", conn_id=phys.conn_id)
        except TimeoutError:
            reason = "idle_timeout"
        except asyncio.CancelledError:
            reason = "cancelled"
            raise
        except Exception as exc:
            reason = f"exception:{exc!r}"
        finally:
            with contextlib.suppress(Exception):
                await ws.close()
            phys.closed.set()
            self._readers.pop(phys.conn_id, None)
            age = (self._clock.monotonic_ns() - phys.opened_mono_ns) / NS_PER_S
            self._emit(
                "disconnected", phys.conn_id, reason=reason, age_s=round(age, 3), frames=phys.frames
            )

    async def _rotate(self, old: _Physical) -> None:
        self._emit("rotation_started", old.conn_id)
        new = await self._open()
        if new is None:
            old.rotate_at_ns = self._clock.monotonic_ns() + int(
                self.settings.rotation_retry_s * NS_PER_S
            )
            self._emit("rotation_failed", old.conn_id, retry_in_s=self.settings.rotation_retry_s)
            return
        self._start_reader(new)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(new.first_frame.wait(), self.settings.first_frame_timeout_s)
        if not new.first_frame.is_set() or new.closed.is_set():
            # New connection is not delivering; keep the old one and retry later.
            await self._close(new)
            old.rotate_at_ns = self._clock.monotonic_ns() + int(
                self.settings.rotation_retry_s * NS_PER_S
            )
            self._emit("rotation_failed", old.conn_id, reason="no_frames_on_new_connection")
            return
        await self._sleep_or_stop(self.settings.overlap_s)
        self._active = new
        await self._close(old)
        self._emit("rotation_completed", new.conn_id, replaced=old.conn_id)

    async def _close(self, phys: _Physical) -> None:
        with contextlib.suppress(Exception):
            await phys.ws.close()
        task = self._readers.get(phys.conn_id)
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(task, timeout=5.0)

    async def _shutdown(self) -> None:
        tasks = list(self._readers.values())
        if self._active is not None:
            with contextlib.suppress(Exception):
                await self._active.ws.close()
        for t in tasks:
            if not t.done():
                t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._emit("stopped", None)
