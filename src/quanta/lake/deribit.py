"""Deribit: raw capture records → normalized tables."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import msgspec
import pyarrow as pa

from quanta.lake.binance_usdm import _rest_path, add_meta
from quanta.lake.table import TS, TableBuilder, f64, ms, schema
from quanta.tools.raw_reader import RawRecord, decode_rest

VENUE = "deribit"

SCHEMAS: dict[str, pa.Schema] = {
    "trades": schema(
        pa.field("ts_exchange", TS),
        pa.field("trade_seq", pa.int64()),
        pa.field("trade_id", pa.string()),
        pa.field("price", pa.float64()),
        pa.field("amount", pa.float64()),
        pa.field("aggressor", pa.string()),
        pa.field("index_price", pa.float64()),
        pa.field("mark_price", pa.float64()),
        pa.field("liquidation", pa.string()),
        pa.field("block_trade_id", pa.string()),
    ),
    "book_deltas": schema(
        pa.field("ts_exchange", TS),
        pa.field("change_id", pa.int64()),
        pa.field("prev_change_id", pa.int64()),
        pa.field("is_snapshot", pa.bool_()),
        pa.field("side", pa.string()),
        pa.field("action", pa.string()),
        pa.field("price", pa.float64()),
        pa.field("amount", pa.float64()),
    ),
    "ticker": schema(
        pa.field("ts_exchange", TS),
        pa.field("mark", pa.float64()),
        pa.field("index", pa.float64()),
        pa.field("last", pa.float64()),
        pa.field("best_bid", pa.float64()),
        pa.field("best_ask", pa.float64()),
        pa.field("open_interest", pa.float64()),
        pa.field("funding_8h", pa.float64()),
        pa.field("current_funding", pa.float64()),
    ),
    "volatility_index": schema(pa.field("ts_exchange", TS), pa.field("volatility", pa.float64())),
    "liquidations": schema(
        pa.field("ts_exchange", TS),
        pa.field("trade_seq", pa.int64()),
        pa.field("liquidated", pa.string()),
        pa.field("aggressor", pa.string()),
        pa.field("price", pa.float64()),
        pa.field("amount", pa.float64()),
        pa.field("completeness", pa.string()),
    ),
    "instruments": schema(
        pa.field("tick_size", pa.float64()),
        pa.field("min_trade_amount", pa.float64()),
        pa.field("contract_size", pa.float64()),
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


class Rpc(msgspec.Struct, frozen=True):
    method: str | None = None
    params: msgspec.Raw = msgspec.Raw()


_rpc = msgspec.json.Decoder(Rpc)


class DeribitNormalizer:
    def __init__(self) -> None:
        self.t = {name: TableBuilder(name, sch) for name, sch in SCHEMAS.items()}
        self.invalid_frames = 0  # undecodable raw frames (stored as ws_invalid)
        self.schema_errors: dict[str, int] = {}  # decodable but unexpected shape → API drift
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
        rpc = _rpc.decode(bytes(rec.p))
        if rpc.method != "subscription" or not rpc.params:
            return  # RPC responses, heartbeats
        params = msgspec.json.decode(bytes(rpc.params))
        channel: str = params["channel"]
        data = params["data"]
        kind = channel.split(".", 1)[0]
        lin = {"venue": VENUE, "ts_arrival": rec.t, "conn_id": rec.c, "conn_seq": rec.n}
        if kind == "book":
            inst = data["instrument_name"]
            key = (inst, int(data["change_id"]), data["type"])
            if key in self._book_seen:
                self.t["book_deltas"].duplicates += 1
                return
            self._book_seen.add(key)
            common = {
                **lin,
                "symbol": inst,
                "ts_exchange": ms(data["timestamp"]),
                "change_id": data["change_id"],
                "prev_change_id": data.get("prev_change_id"),
                "is_snapshot": data["type"] == "snapshot",
            }
            for side, levels in (("bid", data["bids"]), ("ask", data["asks"])):
                for action, price, amount in levels:
                    self.t["book_deltas"].add(
                        None,
                        side=side,
                        action=action,
                        price=float(price),
                        amount=0.0 if action == "delete" else float(amount),
                        **common,
                    )
        elif kind == "trades":
            for x in data:
                inst = x["instrument_name"]
                row = {
                    **lin,
                    "symbol": inst,
                    "ts_exchange": ms(x["timestamp"]),
                    "trade_seq": x["trade_seq"],
                    "price": float(x["price"]),
                    "amount": float(x["amount"]),
                    "aggressor": x.get("direction"),
                }
                if self.t["trades"].add(
                    (inst, x["trade_seq"]),
                    trade_id=str(x.get("trade_id")),
                    index_price=f64(x.get("index_price")),
                    mark_price=f64(x.get("mark_price")),
                    liquidation=x.get("liquidation"),
                    block_trade_id=x.get("block_trade_id"),
                    **row,
                ) and x.get("liquidation"):
                    self.t["liquidations"].add(
                        None, liquidated=x["liquidation"], completeness="full", **row
                    )
        elif kind == "ticker":
            self.t["ticker"].add(
                None,
                symbol=data["instrument_name"],
                ts_exchange=ms(data["timestamp"]),
                mark=f64(data.get("mark_price")),
                index=f64(data.get("index_price")),
                last=f64(data.get("last_price")),
                best_bid=f64(data.get("best_bid_price")),
                best_ask=f64(data.get("best_ask_price")),
                open_interest=f64(data.get("open_interest")),
                funding_8h=f64(data.get("funding_8h")),
                current_funding=f64(data.get("current_funding")),
                **lin,
            )
        elif kind == "deribit_volatility_index":
            self.t["volatility_index"].add(
                (data["index_name"], data["timestamp"]),
                symbol=data["index_name"],
                ts_exchange=ms(data["timestamp"]),
                volatility=f64(data["volatility"]),
                **lin,
            )

    def _rest(self, rec: RawRecord) -> None:
        if rec.k != "rest":
            return
        rp = decode_rest(rec.p)
        if rp.status != 200 or not rp.path.endswith("/public/get_instrument"):
            return
        r = msgspec.json.decode(bytes(rp.body))["result"]
        name = r.get("instrument_name") or str(rp.params.get("instrument_name"))
        state = (r.get("tick_size"), r.get("min_trade_amount"), r.get("contract_size"))
        if self._instrument_state.get(name) == state:
            return
        self._instrument_state[name] = state
        self.t["instruments"].add(
            None,
            venue=VENUE,
            symbol=name,
            ts_arrival=rec.t,
            tick_size=f64(state[0]),
            min_trade_amount=f64(state[1]),
            contract_size=f64(state[2]),
            raw_json=msgspec.json.encode(r).decode(),
        )

    def _meta(self, rec: RawRecord) -> None:
        if rec.k == "meta":
            add_meta(self.t["meta_events"], VENUE, rec)
