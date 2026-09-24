"""Binance USDⓈ-M market data capture.

Principles (plan §8.2):
* Every frame is written **raw first**; parsing only feeds integrity tracking and metrics.
  A parse failure never drops data (the frame is stored as ``ws_invalid``).
* Integrity problems are recorded **in-band** as ``meta`` records (gaps, resyncs,
  lifecycle), so downstream consumers never have to guess whether data is complete.
* The local order book is maintained with the official sequencing algorithm; any gap
  triggers a REST re-snapshot. Duplicate events (make-before-break overlap) are dropped.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import aiohttp
import msgspec

from quanta.core.clock import NS_PER_MS, NS_PER_S, Clock
from quanta.core.log import get_logger
from quanta.core.ticks import Scale, ScaleError
from quanta.marketstate.orderbook import L2Book
from quanta.net.ws import LifecycleEvent, ManagedWebSocket, WsSettings
from quanta.recorder.config import BinanceUsdmCaptureConfig
from quanta.recorder.metrics import RecorderMetrics
from quanta.recorder.records import meta_record, rest_record, ws_invalid_record, ws_record
from quanta.recorder.segment import SegmentWriter
from quanta.venues.binance_usdm import endpoints as ep
from quanta.venues.binance_usdm import messages as msg
from quanta.venues.binance_usdm.ratelimit import WeightBudget
from quanta.venues.binance_usdm.rest import (
    STATS_PATHS,
    BinanceRestError,
    BinanceUsdmRest,
    RateLimitedError,
    RestResponse,
    RestrictedLocationError,
)
from quanta.venues.binance_usdm.sequencing import (
    ContiguousIdTracker,
    DepthSequencer,
    SyncState,
)

log = get_logger(__name__)
VENUE = "binance_usdm"
CHANNELS = ("public_depth", "public_bbo", "market", "rest", "meta")


@dataclass(slots=True)
class DepthState:
    symbol: str
    sequencer: DepthSequencer[msg.DepthUpdate] = field(default_factory=DepthSequencer)
    book: L2Book | None = None
    snapshot_requested: bool = False
    consecutive_failures: int = 0
    audit_requested: bool = False


class Writers:
    """Segment writers per channel for this venue."""

    def __init__(self, writers: dict[str, SegmentWriter], metrics: RecorderMetrics) -> None:
        missing = set(CHANNELS) - set(writers)
        if missing:
            raise ValueError(f"missing writers for channels {sorted(missing)}")
        self._w = writers
        self._metrics = metrics

    def write(self, channel: str, ts_ns: int, line: bytes) -> None:
        self._w[channel].append(ts_ns, line)
        self._metrics.bytes.labels(VENUE, channel).inc(len(line) + 1)

    def meta(self, ts_ns: int, type_: str, **fields: Any) -> None:
        self.write("meta", ts_ns, meta_record(ts_ns, type_, venue=VENUE, **fields))
        self._metrics.meta_events.labels(type_).inc()


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


class BinanceUsdmCapture:
    def __init__(
        self,
        cfg: BinanceUsdmCaptureConfig,
        session: aiohttp.ClientSession,
        clock: Clock,
        writers: Writers,
        metrics: RecorderMetrics,
        ws_settings: WsSettings,
        rng: random.Random | None = None,
    ) -> None:
        self.cfg = cfg
        env = ep.ENVIRONMENTS[cfg.environment]
        self.rest_url = cfg.rest_url or env.rest_url
        self.ws_url = cfg.ws_url or env.ws_url
        self._session = session
        self._clock = clock
        self._w = writers
        self._m = metrics
        self._ws_settings = ws_settings
        self._rng = rng or random.Random()  # noqa: S311 — scheduling jitter
        self.budget = WeightBudget(clock, fraction=cfg.max_weight_fraction)
        self.rest = BinanceUsdmRest(session, self.rest_url, self.budget, clock)
        self.depth: dict[str, DepthState] = {s: DepthState(s) for s in cfg.depth_symbols}
        self.trades: dict[str, ContiguousIdTracker] = {
            s: ContiguousIdTracker() for s in cfg.universe
        }
        self.streams: list[ManagedWebSocket] = []
        self._depth_streams: list[ManagedWebSocket] = []
        self._snapshot_queue: asyncio.Queue[str] = asyncio.Queue()
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self.scales: dict[str, tuple[Scale, Scale]] = {}
        self.restricted = False

    # -- lifecycle -----------------------------------------------------------------------
    async def start(self) -> None:
        self._w.meta(
            self._clock.now_ns(),
            "capture_start",
            rest_url=self.rest_url,
            ws_url=self.ws_url,
            depth_symbols=self.cfg.depth_symbols,
            universe=self.cfg.universe,
        )
        await self._load_exchange_info(initial=True)
        self._build_streams()
        for s in self.streams:
            self._tasks.append(asyncio.create_task(s.run(), name=f"ws:{s.name}"))
        self._tasks.append(asyncio.create_task(self._snapshot_worker(), name="depth-snapshots"))
        self._start_pollers()

    async def stop(self) -> None:
        self._stop.set()
        for s in self.streams:
            s.stop()
        for t in self._tasks:
            if t.get_name().startswith(("poll:", "depth-snapshots")):
                t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._w.meta(self._clock.now_ns(), "capture_stop")

    # -- stream construction -------------------------------------------------------------
    def _build_streams(self) -> None:
        cfg = self.cfg
        per = cfg.streams_per_connection
        groups: list[tuple[str, ep.Route, list[str], Callable[[str, int, int, str], None]]] = []
        depth = [ep.depth_stream(s, cfg.depth_speed_ms) for s in cfg.depth_symbols]
        for i, chunk in enumerate(_chunks(depth, per)):
            groups.append((f"depth{i}", ep.Route.PUBLIC, chunk, self._on_depth_frame))
        bbo = [ep.book_ticker_stream(s) for s in cfg.universe]
        for i, chunk in enumerate(_chunks(bbo, per)):
            groups.append((f"bbo{i}", ep.Route.PUBLIC, chunk, self._on_bbo_frame))
        market: list[str] = []
        for s in cfg.universe:
            market += [ep.agg_trade_stream(s), ep.mark_price_stream(s), ep.kline_stream(s)]
        if cfg.record_force_orders:
            market.append(ep.ALL_FORCE_ORDERS)
        for i, chunk in enumerate(_chunks(market, per)):
            groups.append((f"market{i}", ep.Route.MARKET, chunk, self._on_market_frame))

        for name, route, streams, handler in groups:
            url = ep.combined_stream_url(self.ws_url, route, streams)
            ws = ManagedWebSocket(
                name=f"{VENUE}:{name}",
                url=url,
                session=self._session,
                clock=self._clock,
                on_frame=handler,
                on_lifecycle=self._on_lifecycle,
                settings=self._ws_settings,
            )
            self.streams.append(ws)
            if name.startswith("depth"):
                self._depth_streams.append(ws)
            self._m.connection_up.labels(VENUE, ws.name).set(0)

    # -- frame handlers (synchronous, called from the WS reader) -------------------------
    def _decode(
        self, channel: str, conn_id: str, seq: int, recv_ns: int, text: str
    ) -> msg.Envelope | None:
        payload = text.encode()
        try:
            env = msg.decode_envelope(payload)
        except msgspec.DecodeError:
            self._w.write(channel, recv_ns, ws_invalid_record(recv_ns, conn_id, seq, text))
            self._m.parse_errors.labels(VENUE, "envelope").inc()
            return None
        self._w.write(channel, recv_ns, ws_record(recv_ns, conn_id, seq, payload))
        return env

    def _observe(self, kind: str, stream: str, recv_ns: int, event_ms: int) -> None:
        self._m.messages.labels(VENUE, kind).inc()
        if event_ms:
            self._m.event_latency.labels(VENUE, kind).observe(
                max(0.0, (recv_ns - event_ms * NS_PER_MS) / NS_PER_S)
            )
        self._m.last_message_ts.labels(VENUE, stream).set(recv_ns / NS_PER_S)

    def _on_depth_frame(self, conn_id: str, seq: int, recv_ns: int, text: str) -> None:
        env = self._decode("public_depth", conn_id, seq, recv_ns, text)
        if env is None:
            return
        try:
            ev = msg.decode_depth(env.data)
        except msgspec.DecodeError:
            self._m.parse_errors.labels(VENUE, "depth").inc()
            return
        self._observe("depth", conn_id.split("#")[0], recv_ns, ev.E)
        st = self.depth.get(ev.s)
        if st is None:
            return
        self._sequence_depth(st, ev, recv_ns)

    def _sequence_depth(self, st: DepthState, ev: msg.DepthUpdate, recv_ns: int) -> None:
        res = st.sequencer.on_event(ev)
        if res.duplicates:
            self._m.depth_duplicates.labels(VENUE, st.symbol).inc(res.duplicates)
        if st.book is not None and res.apply:
            try:
                for applied in res.apply:
                    st.book.apply(applied.b, applied.a)
            except (ScaleError, ValueError) as exc:
                # Level not representable at instrument precision: corrupt data or an
                # instrument change we have not seen yet. Resync and reload metadata.
                self._book_error(st, recv_ns, exc)
                return
            self._check_book(st)
        if res.gap is not None:
            g = res.gap
            self._m.depth_gaps.labels(VENUE, st.symbol, g.reason).inc()
            self._m.depth_synced.labels(VENUE, st.symbol).set(0)
            self._w.meta(
                recv_ns,
                "depth_gap",
                symbol=st.symbol,
                reason=g.reason,
                expected_pu=g.expected_pu,
                got_U=g.got_U,
                got_u=g.got_u,
                got_pu=g.got_pu,
            )
            log.warning("depth_gap", symbol=st.symbol, reason=g.reason)
        if st.sequencer.state is SyncState.NEED_SNAPSHOT:
            self._request_snapshot(st)

    def _book_error(self, st: DepthState, ts_ns: int, exc: Exception) -> None:
        self._m.parse_errors.labels(VENUE, "depth_levels").inc()
        self._m.depth_synced.labels(VENUE, st.symbol).set(0)
        self._w.meta(ts_ns, "book_error", symbol=st.symbol, error=str(exc))
        log.error("book_error", symbol=st.symbol, error=str(exc))
        st.sequencer.reset()
        st.book = None
        self._request_snapshot(st)
        self._tasks.append(asyncio.create_task(self._refresh_exchange_info(), name="poll:xinfo"))

    async def _refresh_exchange_info(self) -> None:
        try:
            await self._load_exchange_info(initial=False)
        except (BinanceRestError, aiohttp.ClientError, TimeoutError) as exc:
            log.warning("exchange_info_refresh_failed", error=repr(exc))

    def _check_book(self, st: DepthState) -> None:
        book = st.book
        assert book is not None
        if book.is_crossed:
            self._m.book_crossed.labels(VENUE, st.symbol).inc()
        spread = book.spread_ticks()
        if spread is not None:
            self._m.spread_ticks.labels(VENUE, st.symbol).set(spread)

    def _on_bbo_frame(self, conn_id: str, seq: int, recv_ns: int, text: str) -> None:
        env = self._decode("public_bbo", conn_id, seq, recv_ns, text)
        if env is None:
            return
        try:
            event_ms = msg.decode_event_time(env.data)
        except msgspec.DecodeError:
            self._m.parse_errors.labels(VENUE, "bookTicker").inc()
            return
        self._observe("bookTicker", conn_id.split("#")[0], recv_ns, event_ms)

    def _on_market_frame(self, conn_id: str, seq: int, recv_ns: int, text: str) -> None:
        env = self._decode("market", conn_id, seq, recv_ns, text)
        if env is None:
            return
        kind = msg.stream_kind(env.stream)
        stream = conn_id.split("#")[0]
        try:
            if kind == "aggTrade":
                tr = msg.decode_agg_trade(env.data)
                self._observe(kind, stream, recv_ns, tr.E)
                self._track_trade(tr, recv_ns)
            elif kind == "forceOrder":
                orders = msg.decode_force_orders(env.data)
                for fo in orders:
                    market = {1: "um", 2: "cm"}.get(fo.st or 1, "unknown")
                    self._m.liquidations.labels(VENUE, market).inc()
                self._observe(kind, stream, recv_ns, orders[0].E if orders else 0)
            else:
                self._observe(kind, stream, recv_ns, msg.decode_event_time(env.data))
        except msgspec.DecodeError:
            self._m.parse_errors.labels(VENUE, kind).inc()

    def _track_trade(self, tr: msg.AggTrade, recv_ns: int) -> None:
        tracker = self.trades.get(tr.s)
        if tracker is None:
            return
        dup_before = tracker.duplicates
        gap = tracker.observe(tr.a)
        if tracker.duplicates != dup_before:
            self._m.trade_duplicates.labels(VENUE, tr.s).inc()
        if gap is not None:
            self._m.trade_gaps.labels(VENUE, tr.s).inc()
            self._m.trade_missing.labels(VENUE, tr.s).inc(gap.count)
            self._w.meta(
                recv_ns,
                "trade_gap",
                symbol=tr.s,
                first_missing=gap.first_missing,
                last_missing=gap.last_missing,
                count=gap.count,
            )
            log.warning("trade_gap", symbol=tr.s, missing=gap.count)

    def _on_lifecycle(self, ev: LifecycleEvent) -> None:
        self._w.meta(
            ev.ts_ns,
            "ws_lifecycle",
            stream=ev.name,
            conn_id=ev.conn_id,
            event=ev.event,
            detail=ev.detail,
        )
        self._m.ws_events.labels(VENUE, ev.name, ev.event).inc()
        ws = next((s for s in self.streams if s.name == ev.name), None)
        if ws is None:
            return
        if ev.event == "connected":
            self._m.connection_up.labels(VENUE, ws.name).set(1)
        elif ev.event in ("disconnected", "connect_failed", "stopped"):
            self._m.connection_up.labels(VENUE, ws.name).set(1 if ws.connected else 0)
        if ev.event == "disconnected" and ws in self._depth_streams and not ws.connected:
            # Events were (or will be) missed: every book on this connection must resync.
            for st in self.depth.values():
                if ep.depth_stream(st.symbol, self.cfg.depth_speed_ms) in ws.url:
                    st.sequencer.reset()
                    self._m.depth_synced.labels(VENUE, st.symbol).set(0)
                    self._w.meta(
                        ev.ts_ns, "depth_reset", symbol=st.symbol, reason="depth_connection_lost"
                    )
                    self._request_snapshot(st)

    # -- depth snapshots -----------------------------------------------------------------
    def _request_snapshot(self, st: DepthState) -> None:
        if not st.snapshot_requested:
            st.snapshot_requested = True
            self._snapshot_queue.put_nowait(st.symbol)

    async def _snapshot_worker(self) -> None:
        while not self._stop.is_set():
            symbol = await self._snapshot_queue.get()
            st = self.depth[symbol]
            # Let the stream buffer at least one event so the snapshot can bridge to it.
            for _ in range(20):
                if st.sequencer.buffered or self._stop.is_set():
                    break
                await asyncio.sleep(0.1)
            try:
                await self._resync(st)
            except RestrictedLocationError:
                self._mark_restricted()
                await asyncio.sleep(60)
                self._requeue(st)
            except (BinanceRestError, aiohttp.ClientError, TimeoutError) as exc:
                st.consecutive_failures += 1
                delay = min(30.0, 0.5 * 2 ** min(st.consecutive_failures, 6))
                if isinstance(exc, RateLimitedError):
                    delay = max(delay, exc.retry_after_s)
                log.warning("depth_snapshot_failed", symbol=symbol, error=repr(exc), retry_in=delay)
                await asyncio.sleep(delay)
                self._requeue(st)

    def _requeue(self, st: DepthState) -> None:
        st.snapshot_requested = False
        if st.sequencer.state is SyncState.NEED_SNAPSHOT:
            self._request_snapshot(st)

    async def _resync(self, st: DepthState) -> None:
        resp = await self.rest.depth(st.symbol, self.cfg.snapshot_limit)
        self._record_rest(resp, purpose="depth_resync")
        body = resp.json()
        last_id = int(body["lastUpdateId"])
        res = st.sequencer.on_snapshot(last_id)
        st.snapshot_requested = False
        if res.gap is not None:
            # Snapshot older than buffered stream — fetch a newer one.
            st.consecutive_failures += 1
            self._m.depth_gaps.labels(VENUE, st.symbol, res.gap.reason).inc()
            self._w.meta(
                resp.recv_ns,
                "depth_snapshot_stale",
                symbol=st.symbol,
                last_update_id=last_id,
                first_buffered_U=res.gap.got_U,
            )
            await asyncio.sleep(min(5.0, 0.2 * st.consecutive_failures))
            self._request_snapshot(st)
            return
        scales = self.scales.get(st.symbol)
        if scales is not None:
            book = st.book or L2Book(st.symbol, *scales)
            try:
                book.load_snapshot(body.get("bids", []), body.get("asks", []))
                for ev in res.apply:
                    book.apply(ev.b, ev.a)
            except (ScaleError, ValueError) as exc:
                self._book_error(st, resp.recv_ns, exc)
                return
            st.book = book
            self._check_book(st)
        st.consecutive_failures = 0
        synced = st.sequencer.state is SyncState.LIVE
        self._m.depth_resyncs.labels(VENUE, st.symbol).inc()
        self._m.depth_synced.labels(VENUE, st.symbol).set(1 if synced else 0)
        self._w.meta(
            resp.recv_ns,
            "depth_synced",
            symbol=st.symbol,
            last_update_id=last_id,
            applied_from_buffer=len(res.apply),
            stale_dropped=res.stale,
            live=synced,
        )

    # -- REST pollers --------------------------------------------------------------------
    def _record_rest(self, resp: RestResponse, purpose: str) -> None:
        self._w.write(
            "rest",
            resp.recv_ns,
            rest_record(
                resp.recv_ns,
                path=resp.path,
                params=resp.params,
                status=resp.status,
                sent_ns=resp.sent_ns,
                body=resp.body,
                purpose=purpose,
                headers=resp.headers,
            ),
        )
        endpoint = resp.path.rsplit("/", 1)[-1]
        self._m.rest_requests.labels(VENUE, endpoint, str(resp.status)).inc()
        self._m.rest_latency.labels(VENUE, endpoint).observe(resp.latency_s)
        self._m.rest_used_weight.labels(VENUE).set(self.budget.used)

    def _record_rest_error(self, exc: BinanceRestError, purpose: str) -> None:
        now = self._clock.now_ns()
        self._w.write(
            "rest",
            now,
            rest_record(
                now,
                path=exc.path,
                params={},
                status=exc.status,
                sent_ns=now,
                body=exc.body,
                purpose=purpose,
            ),
        )
        self._m.rest_requests.labels(VENUE, exc.path.rsplit("/", 1)[-1], str(exc.status)).inc()

    def _mark_restricted(self) -> None:
        if not self.restricted:
            log.critical(
                "restricted_location",
                msg="Binance returned HTTP 451 — this host's location is not served. "
                "Move the recorder to an eligible region (plan §19).",
            )
        self.restricted = True
        self._m.restricted_location.labels(VENUE).set(1)

    def _start_pollers(self) -> None:
        p = self.cfg.pollers
        stats_symbols = self.cfg.stats_symbols or self.cfg.universe
        jobs: list[tuple[str, float, Callable[[], Awaitable[None]]]] = [
            ("server_time", p.server_time_s, self._poll_server_time),
            ("exchange_info", p.exchange_info_s, lambda: self._load_exchange_info(False)),
            ("funding_info", p.funding_info_s, self._simple(self.rest.funding_info, "poll")),
            ("premium_index", p.premium_index_s, self._simple(self.rest.premium_index, "poll")),
            (
                "insurance_balance",
                p.insurance_balance_s,
                self._simple(self.rest.insurance_balance, "poll"),
            ),
            ("open_interest", p.open_interest_s, self._poll_open_interest),
            ("stats", p.stats_s, lambda: self._poll_stats(stats_symbols)),
        ]
        if p.depth_audit_s > 0 and self.cfg.depth_symbols:
            jobs.append(("depth_audit", p.depth_audit_s, self._poll_depth_audit))
        for name, interval, fn in jobs:
            if interval > 0:
                self._tasks.append(
                    asyncio.create_task(self._poll_loop(name, interval, fn), name=f"poll:{name}")
                )

    def _simple(
        self, call: Callable[[], Awaitable[RestResponse]], purpose: str
    ) -> Callable[[], Awaitable[None]]:
        async def run() -> None:
            self._record_rest(await call(), purpose)

        return run

    async def _poll_loop(
        self, name: str, interval_s: float, fn: Callable[[], Awaitable[None]]
    ) -> None:
        await asyncio.sleep(self._rng.uniform(0, min(interval_s, 10.0)))
        while not self._stop.is_set():
            delay = interval_s
            try:
                await fn()
            except RestrictedLocationError as exc:
                self._record_rest_error(exc, name)
                self._mark_restricted()
                delay = max(interval_s, 300.0)
            except RateLimitedError as exc:
                self._record_rest_error(exc, name)
                delay = max(interval_s, exc.retry_after_s)
                log.error("rate_limited", poller=name, status=exc.status, retry_after=delay)
            except BinanceRestError as exc:
                self._record_rest_error(exc, name)
                log.warning("poll_failed", poller=name, error=str(exc))
            except (aiohttp.ClientError, TimeoutError) as exc:
                log.warning("poll_failed", poller=name, error=repr(exc))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("poll_crashed", poller=name)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except TimeoutError:
                continue

    async def _poll_server_time(self) -> None:
        resp = await self.rest.server_time()
        self._record_rest(resp, "server_time")
        server_ms = int(resp.json()["serverTime"])
        mid = (resp.sent_ns + resp.recv_ns) // 2
        self._m.clock_offset.labels(VENUE).set((mid - server_ms * NS_PER_MS) / NS_PER_S)
        self._m.server_rtt.labels(VENUE).set(resp.latency_s)
        if self.restricted:
            self.restricted = False
            self._m.restricted_location.labels(VENUE).set(0)

    async def _load_exchange_info(self, initial: bool) -> None:
        try:
            resp = await self.rest.exchange_info()
        except RestrictedLocationError as exc:
            self._record_rest_error(exc, "exchange_info")
            self._mark_restricted()
            if initial:
                return
            raise
        self._record_rest(resp, "exchange_info")
        info = resp.json()
        self._check_universe(info)
        self._update_scales(info)

    def _check_universe(self, info: dict[str, Any]) -> None:
        status = {s.get("symbol"): s.get("status") for s in info.get("symbols", [])}
        bad = {s: status.get(s, "UNKNOWN") for s in self.cfg.universe if status.get(s) != "TRADING"}
        if bad:
            # Binance silently sends nothing for unknown symbols — make it loud instead.
            log.error("universe_symbols_not_trading", symbols=bad)
            self._w.meta(self._clock.now_ns(), "universe_symbols_not_trading", symbols=bad)

    def _update_scales(self, info: dict[str, Any]) -> None:
        for sym in info.get("symbols", []):
            name = sym.get("symbol")
            if name not in self.depth:
                continue
            filters = {f["filterType"]: f for f in sym.get("filters", [])}
            try:
                new = (
                    Scale(filters["PRICE_FILTER"]["tickSize"]),
                    Scale(filters["LOT_SIZE"]["stepSize"]),
                )
            except (KeyError, ValueError):
                log.error("instrument_filters_invalid", symbol=name)
                continue
            old = self.scales.get(name)
            if old is not None and (old[0].step, old[1].step) != (new[0].step, new[1].step):
                st = self.depth[name]
                st.sequencer.reset()
                st.book = None
                self._w.meta(
                    self._clock.now_ns(),
                    "instrument_changed",
                    symbol=name,
                    tick=str(new[0].step),
                    lot=str(new[1].step),
                )
                self._request_snapshot(st)
            self.scales[name] = new

    async def _poll_open_interest(self) -> None:
        for s in self.cfg.universe:
            self._record_rest(await self.rest.open_interest(s), "open_interest")

    async def _poll_stats(self, symbols: list[str]) -> None:
        for s in symbols:
            for path in STATS_PATHS:
                self._record_rest(await self.rest.stats(path, s), "stats")

    async def _poll_depth_audit(self) -> None:
        symbols = self.cfg.depth_symbols
        # spread the (weight-20) snapshots over the interval instead of bursting them
        pause = min(1.0, self.cfg.pollers.depth_audit_s / (2 * len(symbols)))
        for s in symbols:
            self._record_rest(await self.rest.depth(s, self.cfg.snapshot_limit), "depth_audit")
            await asyncio.sleep(pause)
