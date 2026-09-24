"""aiohttp application: scrapes recorder metrics, serves ``/`` and ``/api/status``.

Read-only by design (v1). Bind to 127.0.0.1 and publish to the tailnet with
``tailscale serve`` (ADR-010); nothing listens on a public interface.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

from quanta.core.log import get_logger
from quanta.ui.health import VENUE_NAMES, HealthConfig, evaluate, stream_label, venue_name
from quanta.ui.history import History
from quanta.ui.metrics_reader import bucket_deltas, histogram_quantile, parse
from quanta.ui.sources import VolumeCache, load_access, load_quality

log = get_logger(__name__)
STATE_KEY: web.AppKey[UiState] = web.AppKey("state")
STOP_KEY: web.AppKey[asyncio.Event] = web.AppKey("stop", asyncio.Event)
SCRAPER_KEY: web.AppKey[asyncio.Task[None]] = web.AppKey("scraper")
RUNBOOK_URL = "https://github.com/coniiamca/Bot2-de-ifre/blob/HEAD/docs/runbooks/recorder.md"


@dataclass(slots=True)
class UiConfig:
    metrics_url: str
    data_dir: Path
    access_file: Path | None = None
    scrape_interval_s: float = 10.0
    runbook_url: str = RUNBOOK_URL
    health: HealthConfig = field(default_factory=HealthConfig)


class UiState:
    def __init__(self, cfg: UiConfig) -> None:
        self.cfg = cfg
        self.history = History()
        self.last_ok_ts: float | None = None
        self.last_error: str | None = None
        self.volume = VolumeCache(cfg.data_dir)

    async def scrape_once(self, session: aiohttp.ClientSession) -> None:
        try:
            async with session.get(
                self.cfg.metrics_url, timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                resp.raise_for_status()
                text = await resp.text()
        except (aiohttp.ClientError, TimeoutError) as exc:
            self.last_error = repr(exc)
            return
        now = time.time()
        self.history.add(parse(text, now))
        self.last_ok_ts = now
        self.last_error = None

    async def run(self, stop: asyncio.Event) -> None:
        async with aiohttp.ClientSession() as session:
            while not stop.is_set():
                await self.scrape_once(session)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self.cfg.scrape_interval_s)

    # -- status document ---------------------------------------------------------------------
    def status(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        quality = load_quality(self.cfg.data_dir)
        access_file = self.cfg.access_file or self.cfg.data_dir / "access.json"
        access = load_access(access_file)
        verdict = evaluate(self.history, now, self.last_ok_ts, self.cfg.health, quality, access)
        snap = self.history.latest
        venues: list[dict[str, Any]] = []
        system: dict[str, Any] = {}
        if snap is not None:
            ids = sorted(
                {
                    dict(lbls).get("venue", "?")
                    for lbls in snap.series("quanta_recorder_connection_up")
                }
            )
            old5 = self.history.snapshot_ago(300)
            for v in ids:
                streams = []
                for lbls, up in sorted(
                    snap.series("quanta_recorder_connection_up").items(),
                    key=lambda kv: dict(kv[0]).get("stream", ""),
                ):
                    d = dict(lbls)
                    if d.get("venue") != v:
                        continue
                    last = snap.get(
                        "quanta_recorder_last_message_timestamp_seconds",
                        venue=v,
                        stream=d["stream"],
                    )
                    streams.append(
                        {
                            "stream": d["stream"],
                            "label": stream_label(d["stream"]),
                            "up": up >= 1,
                            "last_msg_age_s": None if last is None else round(now - last, 1),
                        }
                    )
                books = [
                    {"symbol": dict(lbls).get("symbol"), "synced": val >= 1}
                    for lbls, val in sorted(
                        snap.series("quanta_recorder_depth_synced").items(),
                        key=lambda kv: dict(kv[0]).get("symbol", ""),
                    )
                    if dict(lbls).get("venue") == v
                ]
                buckets = bucket_deltas(
                    snap, old5, "quanta_recorder_event_latency_seconds_bucket", venue=v
                )
                p50 = histogram_quantile(0.5, buckets)
                p99 = histogram_quantile(0.99, buckets)
                rates = self.history.rate_series("messages", v)
                venues.append(
                    {
                        "id": v,
                        "name": venue_name(v),
                        "streams": streams,
                        "books": books,
                        "msg_rate": round(rates[-1][1], 1) if rates else None,
                        "latency_p50_ms": None if p50 is None else round(p50 * 1000, 1),
                        "latency_p99_ms": None if p99 is None else round(p99 * 1000, 1),
                        "liquidations_1h": round(self.history.increase("liquidations", v, 3600)),
                        "gaps_1h": round(self.history.increase("depth_gaps", v, 3600)),
                        "trades_missing_1h": round(self.history.increase("trade_missing", v, 3600)),
                        "reconnects_1h": round(self.history.increase("reconnects", v, 3600)),
                        "rate_series": [[round(t), round(r, 2)] for t, r in rates],
                    }
                )
            disk = snap.get("quanta_recorder_disk_free_bytes")
            pending = snap.get("quanta_recorder_pending_upload_files")
            offsets = snap.series("quanta_recorder_clock_offset_seconds")
            system = {
                "disk_free_gb": None if disk is None else round(disk / 1e9, 1),
                "pending_uploads": None if pending is None else int(pending),
                "clock_offset_ms": (
                    round(max(offsets.values(), key=abs) * 1000, 1) if offsets else None
                ),
                "rest_weight": snap.get("quanta_recorder_rest_used_weight", venue="binance_usdm"),
            }
        return {
            "generated_at": datetime.fromtimestamp(now, tz=UTC).isoformat(timespec="seconds"),
            "scrape_ok": self.last_ok_ts is not None and now - self.last_ok_ts < 60,
            "last_scrape_age_s": None if self.last_ok_ts is None else round(now - self.last_ok_ts),
            "verdict": {
                "level": verdict.level,
                "title": verdict.title,
                "issues": [asdict(i) for i in verdict.issues],
            },
            "runbook_url": self.cfg.runbook_url,
            "venue_names": dict(VENUE_NAMES),
            "venues": venues,
            "system": system,
            "access": access,
            "quality": quality,
            "volume": self.volume.get(),
        }


def build_app(cfg: UiConfig) -> web.Application:
    state = UiState(cfg)
    app = web.Application()
    app[STATE_KEY] = state
    index = resources.files("quanta.ui").joinpath("static/index.html").read_bytes()

    async def page(_: web.Request) -> web.Response:
        return web.Response(
            body=index,
            content_type="text/html",
            charset="utf-8",
            headers={"Cache-Control": "no-cache"},
        )

    async def api_status(_: web.Request) -> web.Response:
        doc = await asyncio.to_thread(state.status)
        return web.json_response(doc, headers={"Cache-Control": "no-store"})

    async def healthz(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def on_startup(app: web.Application) -> None:
        stop = asyncio.Event()
        app[STOP_KEY] = stop
        app[SCRAPER_KEY] = asyncio.create_task(state.run(stop))

    async def on_cleanup(app: web.Application) -> None:
        app[STOP_KEY].set()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await app[SCRAPER_KEY]

    app.router.add_get("/", page)
    app.router.add_get("/api/status", api_status)
    app.router.add_get("/healthz", healthz)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def run_ui(cfg: UiConfig, host: str, port: int) -> None:
    log.info("ui_starting", host=host, port=port, metrics_url=cfg.metrics_url)
    web.run_app(build_app(cfg), host=host, port=port, print=None, access_log=None)
