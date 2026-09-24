"""Recorder process: wires config → writers → venue captures → uploader → metrics."""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import socket

import aiohttp

from quanta import __version__
from quanta.core.clock import NS_PER_S, Clock, LiveClock
from quanta.core.config import config_hash
from quanta.core.log import get_logger
from quanta.core.metrics import serve_metrics
from quanta.net.ws import WsSettings
from quanta.recorder.binance_usdm import CHANNELS, VENUE, BinanceUsdmCapture, Writers
from quanta.recorder.config import RecorderConfig
from quanta.recorder.metrics import RecorderMetrics
from quanta.recorder.segment import SegmentInfo, SegmentWriter, recover_all
from quanta.recorder.uploader import Uploader, build_target

log = get_logger(__name__)


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
        self.writers: dict[str, SegmentWriter] = {}
        self.capture: BinanceUsdmCapture | None = None
        self.uploader: Uploader | None = None

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

    async def run(self, stop: asyncio.Event) -> None:
        cfg = self.cfg
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        recovered = await asyncio.to_thread(recover_all, cfg.data_dir)
        if cfg.metrics.enabled:
            serve_metrics(self.metrics.registry, cfg.metrics.host, cfg.metrics.port)
        self.metrics.build.info(
            {"version": __version__, "config_hash": config_hash(cfg), "host": self.host}
        )
        seg = cfg.segments
        for ch in CHANNELS:
            self.writers[ch] = SegmentWriter(
                cfg.data_dir,
                VENUE,
                ch,
                self.clock,
                rotate_ns=seg.rotate_s * NS_PER_S,
                frame_max_bytes=seg.frame_max_bytes,
                zstd_level=seg.zstd_level,
                fsync=seg.fsync,
                on_finalized=self._on_finalized,
                host=self.host,
            )
        writers = Writers(self.writers, self.metrics)
        writers.meta(
            self.clock.now_ns(),
            "recorder_start",
            version=__version__,
            config_hash=config_hash(cfg),
            host=self.host,
            recovered_segments=[r.file for r in recovered],
        )

        flush_stop = asyncio.Event()
        tasks = [
            asyncio.create_task(
                w.run_flusher(seg.flush_interval_s, flush_stop), name=f"flush:{name}"
            )
            for name, w in self.writers.items()
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
            if cfg.binance_usdm.enabled:
                self.capture = BinanceUsdmCapture(
                    cfg.binance_usdm,
                    session,
                    self.clock,
                    writers,
                    self.metrics,
                    self._ws_settings(),
                )
                await self.capture.start()
            log.info("recorder_started", data_dir=str(cfg.data_dir))
            await stop.wait()
            log.info("recorder_stopping")
            if self.capture is not None:
                await self.capture.stop()
        writers.meta(self.clock.now_ns(), "recorder_stop")
        flush_stop.set()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        for w in self.writers.values():
            await w.close()
        if self.uploader is not None:
            await self.uploader.run_once()
        log.info("recorder_stopped")

    async def _disk_monitor(self, stop: asyncio.Event) -> None:
        min_free = self.cfg.min_free_disk_gb * 1e9
        while not stop.is_set():
            free = shutil.disk_usage(self.cfg.data_dir).free
            self.metrics.disk_free.set(free)
            if free < min_free:
                log.critical("disk_space_low", free_gb=round(free / 1e9, 2))
            for name, w in self.writers.items():
                self.metrics.segment_write_errors.labels(VENUE, name).set(w.write_errors)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=30)
