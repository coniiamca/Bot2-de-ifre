"""Recorder process: wires config → writers → venue captures → uploader → metrics."""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import socket
from collections.abc import Callable
from pathlib import Path

import aiohttp

from quanta import __version__
from quanta.core.clock import NS_PER_S, Clock, LiveClock
from quanta.core.config import config_hash
from quanta.core.log import get_logger
from quanta.core.metrics import serve_metrics
from quanta.net.ws import WsSettings
from quanta.recorder.base import VenueCapture, Writers
from quanta.recorder.binance_usdm import BinanceUsdmCapture
from quanta.recorder.bybit_linear import BybitLinearCapture
from quanta.recorder.config import RecorderConfig
from quanta.recorder.deribit import DeribitCapture
from quanta.recorder.diskguard import DiskGuard
from quanta.recorder.metrics import RecorderMetrics
from quanta.recorder.segment import SegmentInfo, SegmentWriter, recover_all
from quanta.recorder.uploader import Uploader, build_target

log = get_logger(__name__)
Factory = Callable[[aiohttp.ClientSession, Writers], VenueCapture]


class RecorderService:
    def __init__(
        self,
        cfg: RecorderConfig,
        clock: Clock | None = None,
        metrics: RecorderMetrics | None = None,
    ) -> None:
        self.cfg = cfg
        self.clock = clock or LiveClock()
        self.metrics = metrics or RecorderMetrics()
        self.host = cfg.host_id or socket.gethostname()
        self.writers: dict[tuple[str, str], SegmentWriter] = {}
        self.captures: dict[str, VenueCapture] = {}
        self.uploader: Uploader | None = None
        gb = 1e9
        self.guard = DiskGuard(
            cfg.min_free_disk_gb * gb, (cfg.min_free_disk_gb + cfg.disk_resume_margin_gb) * gb
        )
        self.venue_writers: dict[str, Writers] = {}
        self._reported_drops: dict[tuple[str, str], int] = {}
        # replaceable in tests
        self.disk_free: Callable[[Path], float] = lambda p: float(shutil.disk_usage(p).free)

    @property
    def capture(self) -> VenueCapture | None:
        """The Binance capture (kept for backwards compatibility in tests/tools)."""
        return self.captures.get("binance_usdm")

    def _ws_settings(self) -> WsSettings:
        w = self.cfg.ws
        return WsSettings(
            max_age_s=w.max_age_s,
            overlap_s=w.overlap_s,
            idle_timeout_s=w.idle_timeout_s,
            heartbeat_s=w.heartbeat_s,
            backoff_max_s=w.backoff_max_s,
            stable_after_s=w.stable_after_s,
        )

    def _on_finalized(self, info: SegmentInfo) -> None:
        self.metrics.segments_finalized.labels(info.venue, info.channel).inc()
        log.info("segment_finalized", file=info.file, records=info.records, bytes=info.bytes)

    def _venue_writers(self, venue: str, channels: tuple[str, ...]) -> Writers:
        seg = self.cfg.segments
        chans: dict[str, SegmentWriter] = {}
        for ch in channels:
            w = SegmentWriter(
                self.cfg.data_dir,
                venue,
                ch,
                self.clock,
                rotate_ns=seg.rotate_s * NS_PER_S,
                frame_max_bytes=seg.frame_max_bytes,
                zstd_level=seg.zstd_level,
                fsync=seg.fsync,
                on_finalized=self._on_finalized,
                host=self.host,
                max_queue_bytes=seg.max_queue_mb * 1024 * 1024,
            )
            self.writers[(venue, ch)] = w
            chans[ch] = w
        return Writers(venue, chans, self.metrics, channels, guard=self.guard)

    async def run(self, stop: asyncio.Event) -> None:
        cfg = self.cfg
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        recovered = await asyncio.to_thread(recover_all, cfg.data_dir)
        if cfg.metrics.enabled:
            serve_metrics(self.metrics.registry, cfg.metrics.host, cfg.metrics.port)
        chash = config_hash(cfg)
        self.metrics.build.info({"version": __version__, "config_hash": chash, "host": self.host})

        ws = self._ws_settings()
        factories: list[tuple[str, tuple[str, ...], Factory]] = []
        if cfg.binance_usdm.enabled:
            factories.append(
                (
                    BinanceUsdmCapture.VENUE,
                    BinanceUsdmCapture.CHANNELS,
                    lambda s, w: BinanceUsdmCapture(
                        cfg.binance_usdm, s, self.clock, w, self.metrics, ws
                    ),
                )
            )
        if cfg.bybit_linear.enabled:
            factories.append(
                (
                    BybitLinearCapture.VENUE,
                    BybitLinearCapture.CHANNELS,
                    lambda s, w: BybitLinearCapture(
                        cfg.bybit_linear, s, self.clock, w, self.metrics, ws
                    ),
                )
            )
        if cfg.deribit.enabled:
            factories.append(
                (
                    DeribitCapture.VENUE,
                    DeribitCapture.CHANNELS,
                    lambda s, w: DeribitCapture(cfg.deribit, s, self.clock, w, self.metrics, ws),
                )
            )
        venue_writers = {v: self._venue_writers(v, chans) for v, chans, _ in factories}
        self.venue_writers = venue_writers
        for vw in venue_writers.values():
            vw.meta(
                self.clock.now_ns(),
                "recorder_start",
                version=__version__,
                config_hash=chash,
                host=self.host,
                recovered_segments=[r.file for r in recovered if r.venue == vw.venue],
            )

        self.metrics.disk_floor.set(self.guard.floor_bytes)
        self.check_disk()  # before any capture starts: never begin writing onto a full disk
        flush_stop = asyncio.Event()
        seg = cfg.segments
        tasks = [
            asyncio.create_task(
                w.run_flusher(seg.flush_interval_s, flush_stop), name=f"flush:{v}/{c}"
            )
            for (v, c), w in self.writers.items()
        ]
        tasks.append(asyncio.create_task(self._disk_monitor(flush_stop), name="disk"))
        if cfg.uploader.enabled:
            self.uploader = Uploader(
                cfg.data_dir,
                build_target(cfg.uploader),
                self.metrics,
                cfg.uploader.local_retention_days,
            )
            tasks.append(
                asyncio.create_task(
                    self.uploader.run(cfg.uploader.scan_interval_s, flush_stop), name="uploader"
                )
            )

        timeout = aiohttp.ClientTimeout(total=30)
        connector = aiohttp.TCPConnector(limit=50, ttl_dns_cache=300)
        async with aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            headers={"User-Agent": f"quanta-recorder/{__version__}"},
        ) as session:
            for venue, _, factory in factories:
                capture = factory(session, venue_writers[venue])
                self.captures[venue] = capture
                await capture.start()
            log.info("recorder_started", data_dir=str(cfg.data_dir), venues=list(self.captures))
            await stop.wait()
            log.info("recorder_stopping")
            for capture in self.captures.values():
                await capture.stop()
        for vw in venue_writers.values():
            vw.meta(self.clock.now_ns(), "recorder_stop")
        flush_stop.set()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        for w in self.writers.values():
            await w.close()
        if self.uploader is not None:
            await self.uploader.run_once()
        log.info("recorder_stopped")

    def check_disk(self) -> None:
        """One disk-guard step: measure free space, switch the guard, publish writer health."""
        now = self.clock.now_ns()
        free = self.disk_free(self.cfg.data_dir)
        self.metrics.disk_free.set(free)
        change = self.guard.update(free, now)
        gb = 1e9
        if change == "on":
            self.metrics.disk_guard_active.set(1)
            log.critical(
                "disk_guard_on",
                free_gb=round(free / gb, 2),
                floor_gb=self.cfg.min_free_disk_gb,
                note="market data is not written until space is freed",
            )
            for vw in self.venue_writers.values():
                vw.guard_dropped = 0
                vw.meta(
                    now,
                    "disk_guard_on",
                    free_bytes=int(free),
                    floor_bytes=int(self.guard.floor_bytes),
                )
        elif change == "off":
            self.metrics.disk_guard_active.set(0)
            paused_s = (now - (self.guard.since_ns or now)) / 1e9
            log.warning("disk_guard_off", free_gb=round(free / gb, 2), paused_s=round(paused_s, 1))
            for vw in self.venue_writers.values():
                vw.meta(
                    now,
                    "disk_guard_off",
                    free_bytes=int(free),
                    paused_s=round(paused_s, 3),
                    dropped_records=vw.guard_dropped,
                )
                vw.guard_dropped = 0
        for (venue, name), w in self.writers.items():
            self.metrics.segment_write_errors.labels(venue, name).set(w.write_errors)
            self.metrics.segment_dropped.labels(venue, name).set(w.dropped_records)
            reported = self._reported_drops.get((venue, name), 0)
            if w.dropped_records > reported and venue in self.venue_writers:
                self._reported_drops[(venue, name)] = w.dropped_records
                self.venue_writers[venue].meta(
                    now,
                    "write_backlog_dropped",
                    channel=name,
                    dropped_records=w.dropped_records - reported,
                )

    async def _disk_monitor(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.cfg.disk_check_s)
            if not stop.is_set():
                self.check_disk()
