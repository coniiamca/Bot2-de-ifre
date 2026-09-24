"""Bybit v5 linear: raw capture records → normalized tables."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import msgspec
import pyarrow as pa

from quanta.lake.binance_usdm import _rest_path, add_meta
from quanta.lake.table import TS, TableBuilder, f64, ms, schema
from quanta.tools.raw_reader import RawRecord, decode_rest

VENUE = "bybit_linear"

SCHEMAS: dict[str, pa.Schema] = {
    "trades": schema(
        pa.field("ts_exchange", TS),
        pa.field("trade_id", pa.string()),
        pa.field("price", pa.float64()),
        pa.field("qty", pa.float64()),
        pa.field("aggressor", pa.string()),
        pa.field("block_trade", pa.bool_()),
    ),
    "book_deltas": schema(
        pa.field("ts_exchange", TS),
        pa.field("ts_matching", TS),
        pa.field("update_id", pa.int64()),
        pa.field("cross_seq", pa.int64()),
        pa.field("is_snapshot", pa.bool_()),
        pa.field("side", pa.string()),
        pa.field("price", pa.float64()),
        pa.field("qty", pa.float64()),
    ),
    "tickers": schema(
        pa.field("ts_exchange", TS),
        pa.field("mark", pa.float64()),
        pa.field("index", pa.float64()),
        pa.field("last", pa.float64()),
        pa.field("funding_rate", pa.float64()),
        pa.field("next_funding_ts", TS),
        pa.field("funding_interval_h", pa.int32()),
        pa.field("funding_cap", pa.float64()),
        pa.field("open_interest", pa.float64()),
        pa.field("open_interest_value", pa.float64()),
        pa.field("bid1", pa.float64()),
        pa.field("ask1", pa.float64()),
        pa.field("basis", pa.float64()),
    ),
    "liquidations": schema(
        pa.field("ts_exchange", TS),
        pa.field("position_side", pa.string()),
        pa.field("qty", pa.float64()),
        pa.field("price", pa.float64()),
        pa.field("completeness", pa.string()),
    ),
    "instruments": schema(
        pa.field("status", pa.string()),
        pa.field("tick_size", pa.string()),
        pa.field("qty_step", pa.string()),
        pa.field("raw_json", pa.string()),
        lineage=False,
    ),
    "meta_events": schema(
        pa.field("type", pa.string()),
        pa.field("stream", pa.string()),
        pa.field("detail_json", pa.string()),
        lineage=False,
    ),
}

_TICKER_FIELDS = {
    "markPrice": "mark",
    "indexPrice": "index",
    "lastPrice": "last",
    "fundingRate": "funding_rate",
    "nextFundingTime": "next_funding_ts",
    "fundingIntervalHour": "funding_interval_h",
    "fundingCap": "funding_cap",
    "openInterest": "open_interest",
    "openInterestValue": "open_interest_value",
    "bid1Price": "bid1",
    "ask1Price": "ask1",
    "basis": "basis",
}


class Msg(msgspec.Struct, frozen=True):
    topic: str | None = None
    type: str | None = None
    ts: int | None = None
    cts: int | None = None
    data: msgspec.Raw = msgspec.Raw()


_msg = msgspec.json.Decoder(Msg)


class BybitLinearNormalizer:
    def __init__(self) -> None:
        self.t = {name: TableBuilder(name, sch) for name, sch in SCHEMAS.items()}
        self.invalid_frames = 0  # undecodable raw frames (stored as ws_invalid)
        self.schema_errors: dict[str, int] = {}  # decodable but unexpected shape → API drift
        self._ticker_state: dict[str, dict[str, Any]] = {}
        self._book_seen: set[tuple[str, int, str]] = set()
        self._instrument_state: dict[str, tuple[Any, ...]] = {}

    def feed(self, channel: str, records: Iterable[RawRecord]) -> None:
        handler = {
            "market": self._ws,
            "public_book": self._ws,
            "rest": self._rest,
            "meta": self._meta,
        }[channel]
        for rec in records:
            if rec.k == "ws_invalid":
                self.invalid_frames += 1
                continue
            try:
                handler(rec)
            except (msgspec.DecodeError, KeyError, TypeError, ValueError, IndexError):
                key = channel if rec.k != "rest" else f"rest:{_rest_path(rec)}"
                self.schema_errors[key] = self.schema_errors.get(key, 0) + 1

    def _ws(self, rec: RawRecord) -> None:
        if rec.k != "ws":
            return
        m = _msg.decode(bytes(rec.p))
        if not m.topic or not m.data:
            return  # subscription acks / pongs
        kind, _, rest = m.topic.partition(".")
        data = msgspec.json.decode(bytes(m.data))
        lin = {"venue": VENUE, "ts_arrival": rec.t, "conn_id": rec.c, "conn_seq": rec.n}
        if kind == "orderbook":
            sym = data["s"]
            key = (sym, int(data["u"]), m.type or "")
            if key in self._book_seen:
                self.t["book_deltas"].duplicates += 1
                return
            self._book_seen.add(key)
            common = {
                **lin,
                "symbol": sym,
                "ts_exchange": ms(m.ts),
                "ts_matching": ms(m.cts),
                "update_id": int(data["u"]),
                "cross_seq": data.get("seq"),
                "is_snapshot": m.type == "snapshot",
            }
            for side, levels in (("bid", data["b"]), ("ask", data["a"])):
                for p, q in levels:
                    self.t["book_deltas"].add(None, side=side, price=f64(p), qty=f64(q), **common)
        elif kind == "publicTrade":
            for x in data:
                self.t["trades"].add(
                    (x["s"], x["i"]),
                    symbol=x["s"],
                    ts_exchange=ms(x["T"]),
                    trade_id=x["i"],
                    price=f64(x["p"]),
                    qty=f64(x["v"]),
                    aggressor=str(x["S"]).lower(),
                    block_trade=bool(x.get("BT", False)),
                    **lin,
                )
        elif kind == "tickers":
            sym = data.get("symbol") or rest
            state = {} if m.type == "snapshot" else dict(self._ticker_state.get(sym, {}))
            state.update({k: v for k, v in data.items() if k in _TICKER_FIELDS})
            self._ticker_state[sym] = state  # deltas carry only changed fields
            row: dict[str, Any] = {v: None for v in _TICKER_FIELDS.values()}
            for k, v in state.items():
                col = _TICKER_FIELDS[k]
                if col == "next_funding_ts":
                    row[col] = ms(v)
                elif col == "funding_interval_h":
                    row[col] = int(v) if v not in (None, "") else None
                else:
                    row[col] = f64(v)
            self.t["tickers"].add(None, symbol=sym, ts_exchange=ms(m.ts), **row, **lin)
        elif kind == "allLiquidation":
            for x in data:
                self.t["liquidations"].add(
                    (x["s"], x["T"], x["S"], x["v"], x["p"]),
                    symbol=x["s"],
                    ts_exchange=ms(x["T"]),
                    position_side=str(x["S"]).lower(),
                    qty=f64(x["v"]),
                    price=f64(x["p"]),
                    completeness="full",
                    **lin,
                )

    def _rest(self, rec: RawRecord) -> None:
        if rec.k != "rest":
            return
        rp = decode_rest(rec.p)
        if rp.status != 200 or not rp.path.endswith("/v5/market/instruments-info"):
            return
        body = msgspec.json.decode(bytes(rp.body))
        for item in body.get("result", {}).get("list", []):
            state = (
                item.get("status"),
                item.get("priceFilter", {}).get("tickSize"),
                item.get("lotSizeFilter", {}).get("qtyStep"),
            )
            sym = item["symbol"]
            if self._instrument_state.get(sym) == state:
                continue
            self._instrument_state[sym] = state
            self.t["instruments"].add(
                None,
                venue=VENUE,
                symbol=sym,
                ts_arrival=rec.t,
                status=state[0],
                tick_size=state[1],
                qty_step=state[2],
                raw_json=msgspec.json.encode(item).decode(),
            )

    def _meta(self, rec: RawRecord) -> None:
        if rec.k == "meta":
            add_meta(self.t["meta_events"], VENUE, rec)
