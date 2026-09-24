"""Exchange access and latency check for a candidate host (plan §0, §5, Phase 0).

Answers, from the machine it runs on and for every venue the recorder captures:
* Does the exchange serve this location? HTTP 451 (Binance) or 403 (Bybit/Deribit
  geo-blocking) on REST or on the WebSocket handshake means no; the host must move.
* REST round-trip distribution and local clock offset vs. exchange server time.
* WebSocket event latency (receive time − exchange event time) on the public streams the
  recorder uses.

``quanta recorder check-access --out <data-dir>/access.json`` writes the result where the
status page (``quanta ui``) shows it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

import aiohttp

from quanta.core.clock import NS_PER_MS, LiveClock
from quanta.recorder.config import RecorderConfig
from quanta.venues.binance_usdm import endpoints as ep

RESTRICTED_STATUS = frozenset({403, 451})
VENUES = ("binance_usdm", "bybit_linear", "deribit")


class Frame(NamedTuple):
    """What a probe learned from one WebSocket text frame."""

    data: bool  # a market-data message (not an ack/heartbeat)
    event_ms: int | None = None  # exchange event time, if the message carries one
    error: str | None = None  # protocol-level rejection (e.g. subscribe failed)


@dataclass(frozen=True, slots=True)
class WsTest:
    url: str
    subscribe: str | None = None  # sent right after connecting


@dataclass(frozen=True, slots=True)
class Probe:
    venue: str
    rest_url: str
    time_url: str
    server_ms: Callable[[Any], int]  # server time (ms) from the decoded JSON body
    ws: dict[str, WsTest]
    frame: Callable[[str], Frame]
    environment: str = "production"


@dataclass(slots=True)
class AccessReport:
    venue: str
    environment: str
    rest_url: str
    reachable: bool = False
    restricted: bool = False
    http_status: int | None = None
    error: str | None = None
    rtt_ms: dict[str, float] = field(default_factory=dict)
    clock_offset_ms: float | None = None
    ws_latency_ms: dict[str, dict[str, float]] = field(default_factory=dict)
    ws_messages: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.reachable and not self.restricted and self.error is None


# -- venue specifics --------------------------------------------------------------------------
def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _binance_frame(text: str) -> Frame:
    m = _json(text)
    data = m.get("data") if isinstance(m, dict) else None
    if not isinstance(data, dict):
        return Frame(False)
    e = data.get("E")
    return Frame(True, int(e) if isinstance(e, int) else None)


def _bybit_frame(text: str) -> Frame:
    m = _json(text)
    if not isinstance(m, dict):
        return Frame(False)
    if m.get("op") == "subscribe" and m.get("success") is False:
        return Frame(False, error=f"subscribe failed: {m.get('ret_msg')}")
    if "topic" not in m:
        return Frame(False)
    ts = m.get("ts")
    return Frame(True, int(ts) if isinstance(ts, int) else None)


def _deribit_frame(text: str) -> Frame:
    m = _json(text)
    if not isinstance(m, dict):
        return Frame(False)
    if "error" in m:
        return Frame(False, error=f"json-rpc error: {m['error']}")
    if m.get("method") != "subscription":
        return Frame(False)
    data = m.get("params", {}).get("data")
    items = data if isinstance(data, list) else [data]
    stamps = [d["timestamp"] for d in items if isinstance(d, dict) and "timestamp" in d]
    return Frame(True, max(stamps) if stamps else None)


def _bybit_time(body: Any) -> int:
    if body.get("retCode") != 0:
        raise ValueError(f"retCode={body.get('retCode')} {body.get('retMsg')}")
    return int(body["result"]["timeNano"]) // 1_000_000


def binance_probe(
    environment: str = "production",
    symbol: str = "BTCUSDT",
    rest_url: str | None = None,
    ws_url: str | None = None,
) -> Probe:
    env = ep.ENVIRONMENTS[environment]
    rest = rest_url or env.rest_url
    ws = ws_url or env.ws_url
    return Probe(
        venue="binance_usdm",
        environment=environment,
        rest_url=rest,
        time_url=f"{rest}/fapi/v1/time",
        server_ms=lambda b: int(b["serverTime"]),
        ws={
            "market": WsTest(
                ep.combined_stream_url(
                    ws,
                    ep.Route.MARKET,
                    [ep.agg_trade_stream(symbol), ep.mark_price_stream(symbol)],
                )
            ),
            "public": WsTest(
                ep.combined_stream_url(ws, ep.Route.PUBLIC, [ep.book_ticker_stream(symbol)])
            ),
        },
        frame=_binance_frame,
    )


def bybit_probe(
    symbol: str = "BTCUSDT",
    rest_url: str = "https://api.bybit.com",
    ws_url: str = "wss://stream.bybit.com/v5/public/linear",
) -> Probe:
    sub = {"op": "subscribe", "args": [f"publicTrade.{symbol}", f"tickers.{symbol}"]}
    return Probe(
        venue="bybit_linear",
        rest_url=rest_url,
        time_url=f"{rest_url}/v5/market/time",
        server_ms=_bybit_time,
        ws={"linear": WsTest(ws_url, json.dumps(sub))},
        frame=_bybit_frame,
    )


def deribit_probe(
    instrument: str = "BTC-PERPETUAL",
    rest_url: str = "https://www.deribit.com/api/v2",
    ws_url: str = "wss://www.deribit.com/ws/api/v2",
) -> Probe:
    sub = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "public/subscribe",
        "params": {"channels": [f"trades.{instrument}.100ms", f"ticker.{instrument}.100ms"]},
    }
    return Probe(
        venue="deribit",
        rest_url=rest_url,
        time_url=f"{rest_url}/public/get_time",
        server_ms=lambda b: int(b["result"]),
        ws={"main": WsTest(ws_url, json.dumps(sub))},
        frame=_deribit_frame,
    )


def probes_from_config(cfg: RecorderConfig | None, venues: list[str] | None = None) -> list[Probe]:
    """Probes for the venues enabled in ``cfg`` (all three with defaults when ``cfg`` is None),
    using the configured URLs and the first configured symbol of each venue."""
    out: list[Probe] = []
    want = set(venues or VENUES)
    if cfg is None:
        cfg = RecorderConfig.model_validate(
            {"data_dir": ".", "bybit_linear": {"enabled": True}, "deribit": {"enabled": True}}
        )
    b, y, d = cfg.binance_usdm, cfg.bybit_linear, cfg.deribit
    if "binance_usdm" in want and b.enabled:
        out.append(binance_probe(b.environment, b.universe[0], b.rest_url, b.ws_url))
    if "bybit_linear" in want and y.enabled:
        out.append(bybit_probe(y.universe[0], y.rest_url, y.ws_url))
    if "deribit" in want and d.enabled:
        out.append(deribit_probe(d.instruments[0], d.rest_url, d.ws_url))
    return out


# -- measurement ------------------------------------------------------------------------------
def _pct(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    qs = statistics.quantiles(values, n=100) if len(values) >= 2 else values * 99
    return {
        "p50": round(qs[49], 2),
        "p90": round(qs[89], 2),
        "max": round(max(values), 2),
        "n": float(len(values)),
    }


class _Restricted(Exception):
    def __init__(self, status: int, where: str, body: str = "") -> None:
        detail = " ".join(body.split())[:160]
        super().__init__(f"HTTP {status} on {where}" + (f" ({detail})" if detail else ""))
        self.status = status


async def _rest_time(
    session: aiohttp.ClientSession, probe: Probe, clock: LiveClock
) -> tuple[float, float]:
    """One server-time call → (round trip ms, local − server offset ms at the RTT midpoint)."""
    sent = clock.now_ns()
    async with session.get(probe.time_url) as r:
        body = await r.text()
        recv = clock.now_ns()
        if r.status in RESTRICTED_STATUS:
            raise _Restricted(r.status, "REST", body)
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}: {body[:200]}")
    server = probe.server_ms(json.loads(body))
    return (recv - sent) / NS_PER_MS, ((sent + recv) // 2 - server * NS_PER_MS) / NS_PER_MS


async def _ws_probe(
    session: aiohttp.ClientSession, test: WsTest, frame: Callable[[str], Frame], seconds: float
) -> tuple[list[float], int]:
    clock = LiveClock()
    lat: list[float] = []
    count = 0
    try:
        ws = await session.ws_connect(test.url, heartbeat=20)
    except aiohttp.WSServerHandshakeError as exc:
        if exc.status in RESTRICTED_STATUS:
            raise _Restricted(exc.status, "WebSocket handshake", exc.message) from exc
        raise
    async with ws:
        if test.subscribe is not None:
            await ws.send_str(test.subscribe)
        loop = asyncio.get_running_loop()
        end = loop.time() + seconds
        while (remaining := end - loop.time()) > 0:
            try:
                m = await ws.receive(timeout=remaining)
            except TimeoutError:
                break
            if m.type is not aiohttp.WSMsgType.TEXT:
                break
            recv = clock.now_ns()
            f = frame(m.data)
            if f.error:
                raise RuntimeError(f.error)
            if not f.data:
                continue
            count += 1
            if f.event_ms:
                lat.append((recv - f.event_ms * NS_PER_MS) / NS_PER_MS)
    return lat, count


async def check_venue(
    probe: Probe,
    samples: int = 10,
    ws_seconds: float = 10.0,
    session: aiohttp.ClientSession | None = None,
) -> AccessReport:
    rep = AccessReport(probe.venue, probe.environment, probe.rest_url)
    async with contextlib.AsyncExitStack() as stack:
        if session is None:
            session = await stack.enter_async_context(
                aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
            )
        clock = LiveClock()
        rtts: list[float] = []
        offsets: list[float] = []
        try:
            for i in range(samples):
                rtt, off = await _rest_time(session, probe, clock)
                rtts.append(rtt)
                offsets.append(off)
                if i + 1 < samples:
                    await asyncio.sleep(0.2)
            rep.reachable = True
        except _Restricted as exc:
            rep.restricted, rep.http_status = True, exc.status
            rep.error = f"{exc} — exchange refuses this location"
            return rep
        except (aiohttp.ClientError, TimeoutError, RuntimeError, ValueError, KeyError) as exc:
            rep.error = f"REST: {exc!r}"
            return rep
        rep.rtt_ms = _pct(rtts)
        rep.clock_offset_ms = round(statistics.median(offsets), 2)
        for name, test in probe.ws.items():
            try:
                lat, count = await _ws_probe(session, test, probe.frame, ws_seconds)
            except _Restricted as exc:
                rep.restricted, rep.http_status = True, exc.status
                rep.error = f"ws {name}: {exc} — exchange refuses this location"
                continue
            except (aiohttp.ClientError, TimeoutError, RuntimeError) as exc:
                rep.error = f"ws {name}: {exc!r}"
                continue
            rep.ws_latency_ms[name] = _pct(lat)
            rep.ws_messages[name] = count
            if count == 0 and rep.error is None:
                rep.error = f"ws {name}: no market data within {ws_seconds:g}s"
    return rep


async def check_all(
    probes: list[Probe], samples: int = 10, ws_seconds: float = 10.0
) -> list[AccessReport]:
    """Venues are independent; check them concurrently."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        return list(
            await asyncio.gather(*(check_venue(p, samples, ws_seconds, session) for p in probes))
        )


# -- output -----------------------------------------------------------------------------------
def report_dict(rep: AccessReport) -> dict[str, Any]:
    return {
        "ok": rep.ok,
        "venue": rep.venue,
        "environment": rep.environment,
        "rest_url": rep.rest_url,
        "reachable": rep.reachable,
        "restricted": rep.restricted,
        "http_status": rep.http_status,
        "error": rep.error,
        "rtt_ms": rep.rtt_ms.get("p50"),
        "rest_rtt_ms": rep.rtt_ms,
        "clock_offset_ms": rep.clock_offset_ms,
        "ws_event_latency_ms": rep.ws_latency_ms,
        "ws_messages": rep.ws_messages,
    }


def summary_line(rep: AccessReport) -> str:
    """One human-readable line per venue (the bootstrap script shows these)."""
    state = "OK" if rep.ok else ("RESTRICTED" if rep.restricted else "FAILED")
    parts = [f"{rep.venue:<13} {state:<10}"]
    if "p50" in rep.rtt_ms:
        parts.append(f"rest_p50={rep.rtt_ms['p50']:.1f}ms")
    if rep.clock_offset_ms is not None:
        parts.append(f"clock_offset={rep.clock_offset_ms:+.1f}ms")
    for name, lat in rep.ws_latency_ms.items():
        if "p50" in lat:
            parts.append(f"ws_{name}_p50={lat['p50']:.1f}ms")
    if rep.error:
        parts.append(rep.error)
    return "  ".join(parts)


def access_document(reports: list[AccessReport], now: datetime | None = None) -> dict[str, Any]:
    """The ``access.json`` document read by the status page."""
    ts = (now or datetime.now(UTC)).astimezone(UTC)
    return {
        "checked_at": ts.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "ok": all(r.ok for r in reports),
        "venues": {r.venue: report_dict(r) for r in reports},
        "note": "clock_offset = local − server (RTT midpoint); keep |offset| < 250 ms",
    }


def write_access(path: Path, doc: dict[str, Any]) -> None:
    """Atomic write so the status page never reads a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
