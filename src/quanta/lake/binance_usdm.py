"""Binance USDⓈ-M: raw capture records → normalized tables (see docs/design §8.3)."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import msgspec
import pyarrow as pa

from quanta.lake.table import TS, SeenKeys, TableBuilder, f64, ms, schema
from quanta.tools.raw_reader import RawRecord, decode_rest
from quanta.venues.binance_usdm import messages as msg

VENUE = "binance_usdm"

SCHEMAS: dict[str, pa.Schema] = {
    "trades": schema(
        pa.field("ts_exchange", TS),
        pa.field("ts_event", TS),
        pa.field("agg_id", pa.int64()),
        pa.field("first_trade_id", pa.int64()),
        pa.field("last_trade_id", pa.int64()),
        pa.field("price", pa.float64()),
        pa.field("qty", pa.float64()),
        pa.field("qty_non_rpi", pa.float64()),
        pa.field("aggressor", pa.string()),
    ),
    "bbo": schema(
        pa.field("ts_exchange", TS),
        pa.field("ts_event", TS),
        pa.field("update_id", pa.int64()),
        pa.field("bid_px", pa.float64()),
        pa.field("bid_qty", pa.float64()),
        pa.field("ask_px", pa.float64()),
        pa.field("ask_qty", pa.float64()),
    ),
    "book_deltas": schema(
        pa.field("ts_exchange", TS),
        pa.field("ts_event", TS),
        pa.field("first_update_id", pa.int64()),
        pa.field("last_update_id", pa.int64()),
        pa.field("prev_update_id", pa.int64()),
        pa.field("is_snapshot", pa.bool_()),
        pa.field("snapshot_purpose", pa.string()),
        pa.field("side", pa.string()),
        pa.field("price", pa.float64()),
        pa.field("qty", pa.float64()),
    ),
    "mark_price": schema(
        pa.field("ts_event", TS),
        pa.field("mark", pa.float64()),
        pa.field("index", pa.float64()),
        pa.field("est_settle", pa.float64()),
        pa.field("funding_rate", pa.float64()),
        pa.field("next_funding_ts", TS),
    ),
    "klines_1m": schema(
        pa.field("open_ts", TS),
        pa.field("close_ts", TS),
        pa.field("open", pa.float64()),
        pa.field("high", pa.float64()),
        pa.field("low", pa.float64()),
        pa.field("close", pa.float64()),
        pa.field("volume", pa.float64()),
        pa.field("quote_volume", pa.float64()),
        pa.field("trades", pa.int64()),
        pa.field("taker_buy_volume", pa.float64()),
        pa.field("taker_buy_quote", pa.float64()),
    ),
    "liquidations": schema(
        pa.field("ts_exchange", TS),
        pa.field("ts_event", TS),
        pa.field("side", pa.string()),
        pa.field("qty", pa.float64()),
        pa.field("price", pa.float64()),
        pa.field("avg_price", pa.float64()),
        pa.field("status", pa.string()),
        pa.field("completeness", pa.string()),
    ),
    "open_interest": schema(
        pa.field("ts_exchange", TS), pa.field("open_interest", pa.float64()), lineage=False
    ),
    "premium_index": schema(
        pa.field("ts_exchange", TS),
        pa.field("mark", pa.float64()),
        pa.field("index", pa.float64()),
        pa.field("est_settle", pa.float64()),
        pa.field("last_funding_rate", pa.float64()),
        pa.field("interest_rate", pa.float64()),
        pa.field("next_funding_ts", TS),
        lineage=False,
    ),
    "funding_events": schema(
        pa.field("funding_ts", TS),
        pa.field("rate", pa.float64()),
        pa.field("mark_price", pa.float64()),
        lineage=False,
    ),
    "funding_info": schema(
        pa.field("interval_h", pa.int32()),
        pa.field("cap", pa.float64()),
        pa.field("floor", pa.float64()),
        lineage=False,
    ),
    "positioning": schema(
        pa.field("ts_exchange", TS),
        pa.field("metric", pa.string()),
        pa.field("field", pa.string()),
        pa.field("value", pa.float64()),
        lineage=False,
    ),
    "instruments": schema(
        pa.field("status", pa.string()),
        pa.field("contract_type", pa.string()),
        pa.field("tick_size", pa.string()),
        pa.field("step_size", pa.string()),
        pa.field("min_qty", pa.string()),
        pa.field("min_notional", pa.string()),
        pa.field("onboard_ts", TS),
        pa.field("delivery_ts", TS),
        pa.field("filters_json", pa.string()),
        lineage=False,
    ),
    "meta_events": schema(
        pa.field("type", pa.string()),
        pa.field("stream", pa.string()),
        pa.field("detail_json", pa.string()),
        lineage=False,
    ),
}

_STATS_METRICS = {
    "/futures/data/openInterestHist": "open_interest_hist",
    "/futures/data/topLongShortAccountRatio": "top_long_short_account",
    "/futures/data/topLongShortPositionRatio": "top_long_short_position",
    "/futures/data/globalLongShortAccountRatio": "global_long_short_account",
    "/futures/data/takerlongshortRatio": "taker_buy_sell",
}


class BinanceUsdmNormalizer:
    def __init__(self) -> None:
        self.t = {name: TableBuilder(name, sch) for name, sch in SCHEMAS.items()}
        self.invalid_frames = 0  # undecodable raw frames (stored as ws_invalid)
        self.schema_errors: dict[str, int] = {}  # decodable but unexpected shape → API drift
        self._instrument_state: dict[str, tuple[Any, ...]] = {}
        self._depth_seen = SeenKeys()  # (symbol, u): overlap duplicates

    def feed(self, channel: str, records: Iterable[RawRecord]) -> None:
        handler = {
            "market": self._market,
            "public_bbo": self._bbo,
            "public_depth": self._depth,
            "rest": self._rest,
            "meta": self._meta,
        }[channel]
        for rec in records:
            if rec.k == "ws_invalid":
                self.invalid_frames += 1
                continue
            try:
                handler(rec)
            except (msgspec.DecodeError, KeyError, TypeError, ValueError):
                key = channel if rec.k != "rest" else f"rest:{_rest_path(rec)}"
                self.schema_errors[key] = self.schema_errors.get(key, 0) + 1

    # -- websocket channels ----------------------------------------------------------------
    def _market(self, rec: RawRecord) -> None:
        if rec.k != "ws":
            return
        env = msg.decode_envelope(bytes(rec.p))
        kind = msg.stream_kind(env.stream)
        lin = {"conn_id": rec.c, "conn_seq": rec.n, "ts_arrival": rec.t, "venue": VENUE}
        if kind == "aggTrade":
            tr = msg.decode_agg_trade(env.data)
            self.t["trades"].add(
                (tr.s, tr.a),
                symbol=tr.s,
                ts_exchange=ms(tr.T),
                ts_event=ms(tr.E),
                agg_id=tr.a,
                first_trade_id=tr.f,
                last_trade_id=tr.l,
                price=f64(tr.p),
                qty=f64(tr.q),
                qty_non_rpi=f64(tr.nq),
                aggressor="sell" if tr.m else "buy",
                **lin,
            )
        elif kind == "markPrice":
            mp = msg.decode_mark_price(env.data)
            self.t["mark_price"].add(
                (mp.s, mp.E),
                symbol=mp.s,
                ts_event=ms(mp.E),
                mark=f64(mp.p),
                index=f64(mp.i),
                est_settle=f64(mp.P),
                funding_rate=f64(mp.r),
                next_funding_ts=ms(mp.T),
                **lin,
            )
        elif kind == "kline":
            ke = msg.decode_kline(env.data)
            k = ke.k
            if k.x:  # closed candles only
                self.t["klines_1m"].add(
                    (ke.s, k.t),
                    symbol=ke.s,
                    open_ts=ms(k.t),
                    close_ts=ms(k.T),
                    open=f64(k.o),
                    high=f64(k.h),
                    low=f64(k.l),
                    close=f64(k.c),
                    volume=f64(k.v),
                    quote_volume=f64(k.q),
                    trades=k.n,
                    taker_buy_volume=f64(k.V),
                    taker_buy_quote=f64(k.Q),
                    **lin,
                )
        elif kind == "forceOrder":
            for fo in msg.decode_force_orders(env.data):
                if fo.st == 2:
                    continue  # COIN-M rows share the all-market stream since 2026-06
                o = fo.o
                self.t["liquidations"].add(
                    (o.s, o.T, o.S, o.q, o.p),
                    symbol=o.s,
                    ts_exchange=ms(o.T),
                    ts_event=ms(fo.E),
                    side=o.S.lower(),
                    qty=f64(o.q),
                    price=f64(o.p),
                    avg_price=f64(o.ap),
                    status=o.X,
                    completeness="sampled_1s",
                    **lin,
                )

    def _bbo(self, rec: RawRecord) -> None:
        if rec.k != "ws":
            return
        env = msg.decode_envelope(bytes(rec.p))
        b = msg.decode_book_ticker(env.data)
        self.t["bbo"].add(
            (b.s, b.u, b.b, b.B, b.a, b.A),
            symbol=b.s,
            ts_exchange=ms(b.T),
            ts_event=ms(b.E),
            update_id=b.u,
            bid_px=f64(b.b),
            bid_qty=f64(b.B),
            ask_px=f64(b.a),
            ask_qty=f64(b.A),
            venue=VENUE,
            ts_arrival=rec.t,
            conn_id=rec.c,
            conn_seq=rec.n,
        )

    def _depth(self, rec: RawRecord) -> None:
        if rec.k != "ws":
            return
        env = msg.decode_envelope(bytes(rec.p))
        d = msg.decode_depth(env.data)
        if not self._depth_seen.add((d.s, d.u), rec.t):
            self.t["book_deltas"].duplicates += 1
            return
        common = {
            "venue": VENUE,
            "symbol": d.s,
            "ts_exchange": ms(d.T),
            "ts_event": ms(d.E),
            "first_update_id": d.U,
            "last_update_id": d.u,
            "prev_update_id": d.pu,
            "is_snapshot": False,
            "ts_arrival": rec.t,
            "conn_id": rec.c,
            "conn_seq": rec.n,
        }
        for side, levels in (("bid", d.b), ("ask", d.a)):
            for p, q in levels:
                self.t["book_deltas"].add(None, side=side, price=f64(p), qty=f64(q), **common)

    # -- REST ------------------------------------------------------------------------------
    def _rest(self, rec: RawRecord) -> None:
        if rec.k != "rest":
            return
        rp = decode_rest(rec.p)
        if rp.status != 200:
            return
        body = msgspec.json.decode(bytes(rp.body))
        base = {"venue": VENUE, "ts_arrival": rec.t}
        path = rp.path
        if path == "/fapi/v1/depth":
            sym = str(rp.params["symbol"])
            common = {
                **base,
                "symbol": sym,
                "ts_exchange": ms(body.get("T")),
                "ts_event": ms(body.get("E")),
                "first_update_id": body["lastUpdateId"],
                "last_update_id": body["lastUpdateId"],
                "prev_update_id": None,
                "is_snapshot": True,
                "snapshot_purpose": rp.purpose,
            }
            for side, levels in (("bid", body["bids"]), ("ask", body["asks"])):
                for p, q in levels:
                    self.t["book_deltas"].add(None, side=side, price=f64(p), qty=f64(q), **common)
        elif path == "/fapi/v1/openInterest":
            self.t["open_interest"].add(
                (body["symbol"], body["time"]),
                symbol=body["symbol"],
                ts_exchange=ms(body["time"]),
                open_interest=f64(body["openInterest"]),
                **base,
            )
        elif path == "/fapi/v1/premiumIndex":
            for x in body if isinstance(body, list) else [body]:
                self.t["premium_index"].add(
                    (x["symbol"], x["time"]),
                    symbol=x["symbol"],
                    ts_exchange=ms(x["time"]),
                    mark=f64(x["markPrice"]),
                    index=f64(x["indexPrice"]),
                    est_settle=f64(x.get("estimatedSettlePrice")),
                    last_funding_rate=f64(x.get("lastFundingRate")),
                    interest_rate=f64(x.get("interestRate")),
                    next_funding_ts=ms(x.get("nextFundingTime")),
                    **base,
                )
        elif path == "/fapi/v1/fundingRate":
            for x in body:
                self.t["funding_events"].add(
                    (x["symbol"], x["fundingTime"]),
                    symbol=x["symbol"],
                    funding_ts=ms(x["fundingTime"]),
                    rate=f64(x["fundingRate"]),
                    mark_price=f64(x.get("markPrice")),
                    **base,
                )
        elif path == "/fapi/v1/fundingInfo":
            for x in body:
                key = (
                    x["symbol"],
                    x.get("fundingIntervalHours"),
                    x.get("adjustedFundingRateCap"),
                    x.get("adjustedFundingRateFloor"),
                )
                self.t["funding_info"].add(
                    key,
                    symbol=x["symbol"],
                    interval_h=x.get("fundingIntervalHours"),
                    cap=f64(x.get("adjustedFundingRateCap")),
                    floor=f64(x.get("adjustedFundingRateFloor")),
                    **base,
                )
        elif path in _STATS_METRICS:
            metric = _STATS_METRICS[path]
            for x in body:
                for fname, value in x.items():
                    if fname in ("symbol", "timestamp", "pair"):
                        continue
                    try:
                        v = float(value)
                    except (TypeError, ValueError):
                        continue
                    self.t["positioning"].add(
                        (x["symbol"], metric, fname, x["timestamp"]),
                        symbol=x["symbol"],
                        ts_exchange=ms(x["timestamp"]),
                        metric=metric,
                        field=fname,
                        value=v,
                        **base,
                    )
        elif path == "/fapi/v1/exchangeInfo":
            for s in body.get("symbols", []):
                self._instrument(s, rec.t)

    def _instrument(self, s: dict[str, Any], ts: int) -> None:
        f = {x["filterType"]: x for x in s.get("filters", [])}
        state = (
            s.get("status"),
            s.get("contractType"),
            f.get("PRICE_FILTER", {}).get("tickSize"),
            f.get("LOT_SIZE", {}).get("stepSize"),
            f.get("LOT_SIZE", {}).get("minQty"),
            f.get("MIN_NOTIONAL", {}).get("notional"),
            s.get("onboardDate"),
            s.get("deliveryDate"),
            json.dumps(s.get("filters", []), sort_keys=True),
        )
        sym = s["symbol"]
        if self._instrument_state.get(sym) == state:
            return  # emit a row only when something changed (SCD2-style versions)
        self._instrument_state[sym] = state
        self.t["instruments"].add(
            None,
            venue=VENUE,
            symbol=sym,
            ts_arrival=ts,
            status=state[0],
            contract_type=state[1],
            tick_size=state[2],
            step_size=state[3],
            min_qty=state[4],
            min_notional=state[5],
            onboard_ts=ms(state[6]),
            delivery_ts=ms(state[7]),
            filters_json=state[8],
        )

    # -- meta ------------------------------------------------------------------------------
    def _meta(self, rec: RawRecord) -> None:
        if rec.k != "meta":
            return
        add_meta(self.t["meta_events"], VENUE, rec)


def _rest_path(rec: RawRecord) -> str:
    try:
        return decode_rest(rec.p).path
    except msgspec.DecodeError:
        return "?"


def add_meta(builder: TableBuilder, venue: str, rec: RawRecord) -> None:
    p = msgspec.json.decode(bytes(rec.p))
    detail = {k: v for k, v in p.items() if k not in ("type", "venue", "symbol", "stream")}
    builder.add(
        None,
        venue=venue,
        symbol=p.get("symbol") or "",
        ts_arrival=rec.t,
        type=p["type"],
        stream=p.get("stream"),
        detail_json=json.dumps(detail, sort_keys=True, default=str),
    )
