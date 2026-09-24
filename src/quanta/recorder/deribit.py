"""Deribit public data capture (perpetual books and trades, volatility index).

Protocol facts (docs.deribit.com, checked 2026-09-24):
* JSON-RPC over ``wss://www.deribit.com/ws/api/v2``; ``public/subscribe`` with channels.
* ``book.{instrument}.{100ms|agg2}``: first a ``snapshot``, then ``change`` notifications
  with ``change_id``/``prev_change_id``; ``prev_change_id == previous change_id`` means no
  message was missed. Levels are ``[action, price, amount]`` (new/change/delete).
* ``trades.{instrument}.100ms``: ``trade_seq`` is the per-instrument trade sequence and
  ``liquidation`` ∈ {M, T, MT} marks liquidation trades → a **complete** liquidation source.
* ``public/set_heartbeat``: the server sends ``test_request``; the client must answer with
  ``public/test`` or the connection is closed.

On a book gap only the book channel is re-subscribed (fresh snapshot); trades keep flowing.
"""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import aiohttp
import msgspec

from quanta.core.clock import Clock
from quanta.core.log import get_logger
from quanta.core.ticks import Scale, ScaleError
from quanta.marketstate.orderbook import L2Book
from quanta.net.ws import LifecycleEvent, ManagedWebSocket, WsSettings
from quanta.recorder.base import VenueCapture, Writers
from quanta.recorder.config import DeribitCaptureConfig
from quanta.recorder.metrics import RecorderMetrics
from quanta.recorder.records import ws_invalid_record, ws_record
from quanta.venues.binance_usdm.sequencing import ContiguousIdTracker

log = get_logger(__name__)
VENUE = "deribit"
CHANNELS = ("public_book", "market", "rest", "meta")


class Rpc(msgspec.Struct, frozen=True):
    method: str | None = None
    params: msgspec.Raw = msgspec.Raw()  # empty when absent (Raw cannot be in a union)
    id: int | None = None
    error: msgspec.Raw = msgspec.Raw()


class Subscription(msgspec.Struct, frozen=True):
    channel: str
    data: msgspec.Raw


class Heartbeat(msgspec.Struct, frozen=True):
    type: str


class BookMsg(msgspec.Struct, frozen=True):
    type: str
    timestamp: int
    instrument_name: str
    change_id: int
    bids: list[tuple[str, Decimal, Decimal]]
    asks: list[tuple[str, Decimal, Decimal]]
    prev_change_id: int | None = None


class Trade(msgspec.Struct, frozen=True):
    trade_seq: int
    instrument_name: str
    timestamp: int
    liquidation: str | None = None


class Stamped(msgspec.Struct, frozen=True):
    timestamp: int = 0


_rpc = msgspec.json.Decoder(Rpc)
_sub = msgspec.json.Decoder(Subscription)
_hb = msgspec.json.Decoder(Heartbeat)
_book = msgspec.json.Decoder(BookMsg)
_trades = msgspec.json.Decoder(list[Trade])
_stamped = msgspec.json.Decoder(Stamped)


def _levels(levels: list[tuple[str, Decimal, Decimal]]) -> list[tuple[str, str]]:
    return [
        (format(p, "f"), "0" if action == "delete" else format(q, "f")) for action, p, q in levels
    ]


@dataclass(slots=True)
class BookState:
    instrument: str
    channel: str
    last_change_id: int | None = None
    book: L2Book | None = None
    resubscribing: bool = False


class DeribitCapture(VenueCapture):
    VENUE = VENUE
    CHANNELS = CHANNELS

    def __init__(
        self,
        cfg: DeribitCaptureConfig,
        session: aiohttp.ClientSession,
        clock: Clock,
        writers: Writers,
        metrics: RecorderMetrics,
        ws_settings: WsSettings,
        rng: random.Random | None = None,
    ) -> None:
        super().__init__(session, clock, writers, metrics, ws_settings, rng)
        self.cfg = cfg
        self.books = {i: BookState(i, f"book.{i}.{cfg.book_interval}") for i in cfg.instruments}
        self.trades = {i: ContiguousIdTracker() for i in cfg.instruments}
        self.scales: dict[str, tuple[Scale, Scale]] = {}
        self._req = 100
        self.ws: ManagedWebSocket | None = None

    def describe(self) -> dict[str, Any]:
        return {
            "ws_url": self.cfg.ws_url,
            "instruments": self.cfg.instruments,
            "volatility_indices": self.cfg.volatility_indices,
        }

    def _channels(self) -> list[str]:
        chans: list[str] = []
        for i in self.cfg.instruments:
            chans += [
                f"book.{i}.{self.cfg.book_interval}",
                f"trades.{i}.100ms",
                f"ticker.{i}.100ms",
            ]
        chans += [f"deribit_volatility_index.{x}" for x in self.cfg.volatility_indices]
        return chans

    async def prepare(self) -> None:
        try:
            await self._load_instruments()
        except (aiohttp.ClientError, TimeoutError) as exc:
            log.warning("get_instrument_failed", venue=VENUE, error=repr(exc))
        self.ws = self.add_stream("main", self.cfg.ws_url, self._on_frame, self._on_open)

    def _rpc_text(self, method: str, params: dict[str, Any]) -> str:
        self._req += 1
        return msgspec.json.encode(
            {"jsonrpc": "2.0", "id": self._req, "method": method, "params": params}
        ).decode()

    async def _on_open(self, conn_id: str) -> None:
        assert self.ws is not None
        await self.ws.send_text(
            conn_id, self._rpc_text("public/set_heartbeat", {"interval": self.cfg.heartbeat_s})
        )
        await self.ws.send_text(
            conn_id, self._rpc_text("public/subscribe", {"channels": self._channels()})
        )

    # -- frames --------------------------------------------------------------------------
    def _on_frame(self, conn_id: str, seq: int, recv_ns: int, text: str) -> None:
        payload = text.encode()
        try:
            rpc = _rpc.decode(payload)
            sub = (
                _sub.decode(rpc.params)
                if rpc.method == "subscription" and rpc.params is not None
                else None
            )
        except msgspec.DecodeError:
            # never dropped: stored verbatim as ws_invalid
            self._w.write("market", recv_ns, ws_invalid_record(recv_ns, conn_id, seq, text))
            self._m.parse_errors.labels(VENUE, "message").inc()
            return
        channel = "public_book" if sub is not None and sub.channel.startswith("book.") else "market"
        self._w.write(channel, recv_ns, ws_record(recv_ns, conn_id, seq, payload))
        try:
            if rpc.method == "heartbeat" and rpc.params:
                if _hb.decode(rpc.params).type == "test_request":
                    self.spawn(self._reply_test(conn_id), "heartbeat")
                return
            if rpc.error:
                self._w.meta(
                    recv_ns,
                    "rpc_error",
                    conn_id=conn_id,
                    id=rpc.id,
                    error=msgspec.json.decode(rpc.error),
                )
                log.error("rpc_error", venue=VENUE, id=rpc.id)
                return
            if sub is None:
                return
            kind = sub.channel.split(".", 1)[0]
            stream = conn_id.split("#")[0]
            if kind == "book":
                bm = _book.decode(sub.data)
                self.observe("book", stream, recv_ns, bm.timestamp)
                st = self.books.get(bm.instrument_name)
                if st is not None:
                    self._apply_book(st, bm, recv_ns)
            elif kind == "trades":
                trades = _trades.decode(sub.data)
                self.observe("trades", stream, recv_ns, trades[-1].timestamp if trades else 0)
                self._track_trades(trades, recv_ns)
            else:
                self.observe(kind, stream, recv_ns, _stamped.decode(sub.data).timestamp)
        except msgspec.DecodeError:
            self._m.parse_errors.labels(VENUE, "message").inc()

    async def _reply_test(self, conn_id: str) -> None:
        assert self.ws is not None
        await self.ws.send_text(conn_id, self._rpc_text("public/test", {}))

    def _apply_book(self, st: BookState, bm: BookMsg, recv_ns: int) -> None:
        if bm.type == "snapshot":
            if (
                st.last_change_id is not None
                and bm.change_id <= st.last_change_id
                and not st.resubscribing
            ):
                self._m.depth_duplicates.labels(VENUE, st.instrument).inc()
                return
            st.resubscribing = False
            st.last_change_id = bm.change_id
            if self._load(st, bm, recv_ns, snapshot=True):
                self._m.depth_resyncs.labels(VENUE, st.instrument).inc()
                self._m.depth_synced.labels(VENUE, st.instrument).set(1)
            return
        if st.last_change_id is None or st.resubscribing:
            return  # waiting for a snapshot
        if bm.change_id <= st.last_change_id:
            self._m.depth_duplicates.labels(VENUE, st.instrument).inc()
            return
        if bm.prev_change_id != st.last_change_id:
            self._m.depth_gaps.labels(VENUE, st.instrument, "prev_change_id_mismatch").inc()
            self._m.depth_synced.labels(VENUE, st.instrument).set(0)
            self._w.meta(
                recv_ns,
                "depth_gap",
                symbol=st.instrument,
                reason="prev_change_id_mismatch",
                expected=st.last_change_id,
                got_prev=bm.prev_change_id,
                got=bm.change_id,
            )
            log.warning("depth_gap", venue=VENUE, instrument=st.instrument)
            self._resubscribe(st)
            return
        st.last_change_id = bm.change_id
        self._load(st, bm, recv_ns, snapshot=False)

    def _load(self, st: BookState, bm: BookMsg, recv_ns: int, *, snapshot: bool) -> bool:
        scales = self.scales.get(st.instrument)
        if scales is None:
            return True
        if st.book is None and not snapshot:
            return True
        try:
            if snapshot:
                st.book = st.book or L2Book(st.instrument, *scales)
                st.book.load_snapshot(_levels(bm.bids), _levels(bm.asks))
            else:
                assert st.book is not None
                st.book.apply(_levels(bm.bids), _levels(bm.asks))
        except (ScaleError, ValueError) as exc:
            self._m.parse_errors.labels(VENUE, "depth_levels").inc()
            self._m.depth_synced.labels(VENUE, st.instrument).set(0)
            self._w.meta(recv_ns, "book_error", symbol=st.instrument, error=str(exc))
            st.book = None
            self._resubscribe(st)
            return False
        if st.book.is_crossed:
            self._m.book_crossed.labels(VENUE, st.instrument).inc()
        spread = st.book.spread_ticks()
        if spread is not None:
            self._m.spread_ticks.labels(VENUE, st.instrument).set(spread)
        return True

    def _resubscribe(self, st: BookState) -> None:
        """Unsubscribe + subscribe the book channel only → fresh snapshot, trades unaffected."""
        if st.resubscribing or self.ws is None or self.ws.active_conn_id is None:
            return
        st.resubscribing = True
        conn_id = self.ws.active_conn_id

        async def run() -> None:
            assert self.ws is not None
            await self.ws.send_text(
                conn_id, self._rpc_text("public/unsubscribe", {"channels": [st.channel]})
            )
            await self.ws.send_text(
                conn_id, self._rpc_text("public/subscribe", {"channels": [st.channel]})
            )

        self.spawn(run(), "resubscribe")

    def _track_trades(self, trades: list[Trade], recv_ns: int) -> None:
        for t in trades:
            if t.liquidation:
                self._m.liquidations.labels(VENUE, t.liquidation).inc()
            tracker = self.trades.get(t.instrument_name)
            if tracker is None:
                continue
            dup = tracker.duplicates
            gap = tracker.observe(t.trade_seq)
            if tracker.duplicates != dup:
                self._m.trade_duplicates.labels(VENUE, t.instrument_name).inc()
            if gap is not None:
                self._m.trade_gaps.labels(VENUE, t.instrument_name).inc()
                self._m.trade_missing.labels(VENUE, t.instrument_name).inc(gap.count)
                self._w.meta(
                    recv_ns,
                    "trade_gap",
                    symbol=t.instrument_name,
                    sequence="trade_seq",
                    first_missing=gap.first_missing,
                    last_missing=gap.last_missing,
                    count=gap.count,
                )

    def after_lifecycle(self, ev: LifecycleEvent, ws: ManagedWebSocket) -> None:
        if ev.event == "disconnected" and not ws.connected:
            for st in self.books.values():
                st.last_change_id = None
                st.resubscribing = False
                self._m.depth_synced.labels(VENUE, st.instrument).set(0)
            self._w.meta(ev.ts_ns, "depth_reset", stream=ws.name, reason="connection_lost")

    # -- REST ----------------------------------------------------------------------------
    def pollers(self) -> list[tuple[str, float, Callable[[], Awaitable[None]]]]:
        return [("get_instrument", self.cfg.instruments_info_s, self._load_instruments)]

    async def _load_instruments(self) -> None:
        for name in self.cfg.instruments:
            status, body = await self.http_get(
                f"{self.cfg.rest_url}/public/get_instrument",
                {"instrument_name": name},
                "get_instrument",
            )
            if status != 200:
                continue
            try:
                r = msgspec.json.decode(body)["result"]
                self.scales[name] = (
                    Scale(format(Decimal(str(r["tick_size"])), "f")),
                    Scale(format(Decimal(str(r["min_trade_amount"])), "f")),
                )
            except (KeyError, TypeError, ValueError, msgspec.DecodeError):
                log.error("get_instrument_invalid", venue=VENUE, instrument=name)
