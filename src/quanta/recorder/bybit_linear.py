"""Bybit v5 public linear (USDT perpetual) capture.

Protocol facts (bybit-exchange.github.io/docs/v5, checked 2026-09-24):
* ``wss://stream.bybit.com/v5/public/linear``; subscribe with ``{"op":"subscribe","args":[…]}``
  (args ≤ 21,000 characters per connection); client must ``{"op":"ping"}`` every 20 s.
* ``orderbook.{depth}.{symbol}``: a ``snapshot`` resets the book, ``delta`` updates it.
  ``u = 1`` in a snapshot means the service restarted. ``u`` is *not documented* as
  strictly +1 between deltas, so non-contiguous ``u`` is counted (``u_jumps``) but is not
  treated as a gap; stale/duplicate updates (``u <= last``) are dropped.
* ``allLiquidation.{symbol}`` delivers **all** liquidations (unlike Binance's sampled feed).
"""

from __future__ import annotations

import dataclasses
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import aiohttp
import msgspec

from quanta.core.clock import Clock
from quanta.core.log import get_logger
from quanta.core.ticks import Scale, ScaleError
from quanta.marketstate.orderbook import L2Book
from quanta.net.ws import LifecycleEvent, ManagedWebSocket, WsSettings
from quanta.recorder.base import VenueCapture, Writers, chunks
from quanta.recorder.config import BybitLinearCaptureConfig
from quanta.recorder.metrics import RecorderMetrics

log = get_logger(__name__)
VENUE = "bybit_linear"
CHANNELS = ("public_book", "market", "rest", "meta")
SUBSCRIBE_BATCH = 10


class Msg(msgspec.Struct, frozen=True):
    topic: str | None = None
    type: str | None = None
    ts: int | None = None
    data: msgspec.Raw = msgspec.Raw()  # empty when absent (Raw cannot be in a union)
    op: str | None = None
    success: bool | None = None
    ret_msg: str | None = None


class BookData(msgspec.Struct, frozen=True):
    s: str
    b: list[tuple[str, str]]
    a: list[tuple[str, str]]
    u: int
    seq: int | None = None


class Liquidation(msgspec.Struct, frozen=True):
    T: int
    s: str
    S: str
    v: str
    p: str


_msg = msgspec.json.Decoder(Msg)
_book = msgspec.json.Decoder(BookData)
_liqs = msgspec.json.Decoder(list[Liquidation])


@dataclass(slots=True)
class BookState:
    symbol: str
    last_u: int | None = None
    book: L2Book | None = None
    u_jumps: int = 0


class BybitLinearCapture(VenueCapture):
    VENUE = VENUE
    CHANNELS = CHANNELS

    def __init__(
        self,
        cfg: BybitLinearCaptureConfig,
        session: aiohttp.ClientSession,
        clock: Clock,
        writers: Writers,
        metrics: RecorderMetrics,
        ws_settings: WsSettings,
        rng: random.Random | None = None,
    ) -> None:
        super().__init__(session, clock, writers, metrics, ws_settings, rng)
        self.cfg = cfg
        self.books: dict[str, BookState] = {s: BookState(s) for s in cfg.book_symbols}
        self.scales: dict[str, tuple[Scale, Scale]] = {}
        self._req = 0
        self._book_streams: list[ManagedWebSocket] = []
        self._book_stream_of: dict[str, ManagedWebSocket] = {}

    def describe(self) -> dict[str, Any]:
        return {
            "ws_url": self.cfg.ws_url,
            "book_symbols": self.cfg.book_symbols,
            "universe": self.cfg.universe,
        }

    async def prepare(self) -> None:
        try:
            await self._load_instruments()
        except (aiohttp.ClientError, TimeoutError) as exc:
            log.warning("instruments_info_failed", venue=VENUE, error=repr(exc))
        settings = dataclasses.replace(
            self._ws_settings,
            app_ping_interval_s=self.cfg.ping_interval_s,
            app_ping_payload='{"op":"ping"}',
        )
        book_topics = [f"orderbook.{self.cfg.book_depth}.{s}" for s in self.cfg.book_symbols]
        market_topics: list[str] = []
        for s in self.cfg.universe:
            market_topics += [f"publicTrade.{s}", f"tickers.{s}"]
            if self.cfg.record_liquidations:
                market_topics.append(f"allLiquidation.{s}")
        per = self.cfg.topics_per_connection
        for i, topics in enumerate(chunks(book_topics, per)):
            ws = self.add_stream(
                f"book{i}",
                self.cfg.ws_url,
                self._on_book_frame,
                self._subscriber(f"book{i}", topics),
                settings,
            )
            self._book_streams.append(ws)
            for t in topics:
                self._book_stream_of[t.rsplit(".", 1)[-1]] = ws
        for i, topics in enumerate(chunks(market_topics, per)):
            self.add_stream(
                f"market{i}",
                self.cfg.ws_url,
                self._on_market_frame,
                self._subscriber(f"market{i}", topics),
                settings,
            )

    def _subscriber(self, name: str, topics: list[str]) -> Callable[[str], Awaitable[None]]:
        async def on_open(conn_id: str) -> None:
            ws = self.stream_by_conn(conn_id)
            assert ws is not None
            for batch in chunks(topics, SUBSCRIBE_BATCH):
                self._req += 1
                req = {"req_id": f"{name}-{self._req}", "op": "subscribe", "args": batch}
                await ws.send_text(conn_id, msgspec.json.encode(req).decode())

        return on_open

    # -- frames --------------------------------------------------------------------------
    def _decode(self, channel: str, conn_id: str, seq: int, recv_ns: int, text: str) -> Msg | None:
        payload = self.write_ws(channel, conn_id, seq, recv_ns, text)
        if payload is None:
            return None
        try:
            m = _msg.decode(payload)
        except msgspec.DecodeError:
            self._m.parse_errors.labels(VENUE, "message").inc()
            return None
        if m.op == "subscribe" and m.success is False:
            self._w.meta(recv_ns, "subscribe_failed", conn_id=conn_id, ret_msg=m.ret_msg)
            log.error("subscribe_failed", venue=VENUE, ret_msg=m.ret_msg)
        return m

    def _on_book_frame(self, conn_id: str, seq: int, recv_ns: int, text: str) -> None:
        m = self._decode("public_book", conn_id, seq, recv_ns, text)
        if m is None or m.topic is None or not m.data:
            return
        try:
            data = _book.decode(m.data)
        except msgspec.DecodeError:
            self._m.parse_errors.labels(VENUE, "orderbook").inc()
            return
        self.observe("orderbook", conn_id.split("#")[0], recv_ns, m.ts)
        st = self.books.get(data.s)
        if st is not None:
            self._apply_book(st, m.type or "", data, recv_ns)

    def _apply_book(self, st: BookState, kind: str, data: BookData, recv_ns: int) -> None:
        if kind == "snapshot":
            restart = data.u == 1
            if st.last_u is not None and data.u <= st.last_u and not restart:
                # Same state again from an overlapping connection — keep the newer one.
                self._m.depth_duplicates.labels(VENUE, st.symbol).inc()
                return
            if restart:
                self._w.meta(recv_ns, "book_reset", symbol=st.symbol, reason="service_restart")
            st.last_u = data.u
            if not self._load(st, data, recv_ns, snapshot=True):
                return
            self._m.depth_resyncs.labels(VENUE, st.symbol).inc()
            self._m.depth_synced.labels(VENUE, st.symbol).set(1)
            return
        if st.last_u is None:
            return  # delta before the first snapshot of this subscription
        if data.u <= st.last_u:
            self._m.depth_duplicates.labels(VENUE, st.symbol).inc()
            return
        if data.u != st.last_u + 1:
            st.u_jumps += 1
        st.last_u = data.u
        self._load(st, data, recv_ns, snapshot=False)

    def _load(self, st: BookState, data: BookData, recv_ns: int, *, snapshot: bool) -> bool:
        scales = self.scales.get(st.symbol)
        if scales is None:
            return True  # sequence tracking only until instrument metadata is known
        if st.book is None and not snapshot:
            return True  # book only (re)starts from a snapshot
        try:
            if snapshot:
                st.book = st.book or L2Book(st.symbol, *scales)
                st.book.load_snapshot(data.b, data.a)
            else:
                assert st.book is not None
                st.book.apply(data.b, data.a)
        except (ScaleError, ValueError) as exc:
            self._m.parse_errors.labels(VENUE, "depth_levels").inc()
            self._m.depth_synced.labels(VENUE, st.symbol).set(0)
            self._w.meta(recv_ns, "book_error", symbol=st.symbol, error=str(exc))
            st.book = None
            st.last_u = None  # wait for the next snapshot
            ws = self._book_stream_of.get(st.symbol)
            if ws is not None:  # resubscribing delivers a fresh snapshot
                self.spawn(ws.force_reconnect("book_error"), "reconnect")
            return False
        if st.book.is_crossed:
            self._m.book_crossed.labels(VENUE, st.symbol).inc()
        spread = st.book.spread_ticks()
        if spread is not None:
            self._m.spread_ticks.labels(VENUE, st.symbol).set(spread)
        return True

    def _on_market_frame(self, conn_id: str, seq: int, recv_ns: int, text: str) -> None:
        m = self._decode("market", conn_id, seq, recv_ns, text)
        if m is None or m.topic is None:
            return
        kind = m.topic.split(".", 1)[0]
        self.observe(kind, conn_id.split("#")[0], recv_ns, m.ts)
        if kind == "allLiquidation" and m.data:
            try:
                self._m.liquidations.labels(VENUE, "linear").inc(len(_liqs.decode(m.data)))
            except msgspec.DecodeError:
                self._m.parse_errors.labels(VENUE, kind).inc()

    def after_lifecycle(self, ev: LifecycleEvent, ws: ManagedWebSocket) -> None:
        if ev.event == "disconnected" and ws in self._book_streams and not ws.connected:
            for st in self.books.values():
                st.last_u = None
                self._m.depth_synced.labels(VENUE, st.symbol).set(0)
            self._w.meta(ev.ts_ns, "depth_reset", stream=ws.name, reason="connection_lost")

    # -- REST ----------------------------------------------------------------------------
    def pollers(self) -> list[tuple[str, float, Callable[[], Awaitable[None]]]]:
        return [("instruments_info", self.cfg.instruments_info_s, self._load_instruments)]

    async def _load_instruments(self) -> None:
        for s in self.cfg.book_symbols:
            status, body = await self.http_get(
                f"{self.cfg.rest_url}/v5/market/instruments-info",
                {"category": "linear", "symbol": s},
                "instruments_info",
            )
            if status != 200:
                log.warning("instruments_info_http", venue=VENUE, symbol=s, status=status)
                continue
            try:
                item = msgspec.json.decode(body)["result"]["list"][0]
                self.scales[s] = (
                    Scale(item["priceFilter"]["tickSize"]),
                    Scale(item["lotSizeFilter"]["qtyStep"]),
                )
            except (KeyError, IndexError, TypeError, ValueError, msgspec.DecodeError):
                log.error("instruments_info_invalid", venue=VENUE, symbol=s)
