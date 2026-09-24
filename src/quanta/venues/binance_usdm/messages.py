"""Typed views over Binance USDⓈ-M market stream payloads (only fields we use).

Decoding is partial and lazy: the envelope is decoded first with ``data`` kept raw, then
``data`` is decoded into the struct for its stream type. Unknown fields are ignored, so
additive API changes do not break decoding; removed fields surface as decode errors which
are counted and alerted on (API drift, plan §2.7).
"""

from __future__ import annotations

import msgspec


class Envelope(msgspec.Struct, frozen=True):
    stream: str
    data: msgspec.Raw


class DepthUpdate(msgspec.Struct, frozen=True):
    E: int  # event time (ms)
    T: int  # transaction time (ms)
    s: str
    U: int  # first update id in event
    u: int  # final update id in event
    pu: int  # final update id of previous event
    b: list[tuple[str, str]]
    a: list[tuple[str, str]]


class AggTrade(msgspec.Struct, frozen=True):
    E: int
    s: str
    a: int  # aggregate trade id
    p: str
    q: str
    f: int  # first trade id
    l: int  # last trade id  # noqa: E741 — exchange field name
    T: int  # trade time (ms)
    m: bool  # buyer is maker → aggressor is the seller
    nq: str | None = None  # quantity excluding RPI orders (since 2025-12-31)


class BookTicker(msgspec.Struct, frozen=True):
    u: int
    E: int
    T: int
    s: str
    b: str
    B: str
    a: str
    A: str


class MarkPrice(msgspec.Struct, frozen=True):
    E: int
    s: str
    p: str  # mark price
    i: str  # index price
    r: str  # current funding rate estimate
    T: int  # next funding time (ms)
    P: str | None = None  # estimated settle price


class ForceOrderInner(msgspec.Struct, frozen=True):
    s: str
    S: str  # side
    q: str
    p: str
    ap: str | None = None
    X: str | None = None
    T: int = 0


class ForceOrder(msgspec.Struct, frozen=True):
    E: int
    o: ForceOrderInner
    st: int | None = None  # 1 = USDⓈ-M, 2 = COIN-M (all-market streams mix both since 2026-06)


class EventTime(msgspec.Struct, frozen=True):
    """Minimal view used when only the event time matters (latency metrics)."""

    E: int = 0


_envelope_decoder = msgspec.json.Decoder(Envelope)
_depth_decoder = msgspec.json.Decoder(DepthUpdate)
_agg_decoder = msgspec.json.Decoder(AggTrade)
_bbo_decoder = msgspec.json.Decoder(BookTicker)
_mark_decoder = msgspec.json.Decoder(MarkPrice)
_force_decoder = msgspec.json.Decoder(ForceOrder)
_force_arr_decoder = msgspec.json.Decoder(list[ForceOrder] | ForceOrder)
_event_time_decoder = msgspec.json.Decoder(EventTime)


def decode_envelope(raw: bytes | str) -> Envelope:
    return _envelope_decoder.decode(raw)


def decode_depth(raw: msgspec.Raw | bytes) -> DepthUpdate:
    return _depth_decoder.decode(raw)


def decode_agg_trade(raw: msgspec.Raw | bytes) -> AggTrade:
    return _agg_decoder.decode(raw)


def decode_book_ticker(raw: msgspec.Raw | bytes) -> BookTicker:
    return _bbo_decoder.decode(raw)


def decode_mark_price(raw: msgspec.Raw | bytes) -> MarkPrice:
    return _mark_decoder.decode(raw)


def decode_force_orders(raw: msgspec.Raw | bytes) -> list[ForceOrder]:
    value = _force_arr_decoder.decode(raw)
    return value if isinstance(value, list) else [value]


def decode_event_time(raw: msgspec.Raw | bytes) -> int:
    return _event_time_decoder.decode(raw).E


def stream_kind(stream: str) -> str:
    """Classify a stream name into a coarse kind used for dispatch and metric labels."""
    if stream.startswith("!"):
        return stream[1:].split("@", 1)[0]
    name = stream.split("@", 1)[1] if "@" in stream else stream
    for kind in ("depth", "rpiDepth", "bookTicker", "aggTrade", "markPrice", "kline", "forceOrder"):
        if name.startswith(kind):
            return kind
    return name.split("@", 1)[0]
