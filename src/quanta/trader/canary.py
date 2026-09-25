"""Canary: small, regular plumbing test cycles on the **demo** account (not a strategy).

One cycle on the smallest allowed size: post-only limit buy at the best bid → if not
filled in time, cancel → if filled, a protective stop at the exchange → hold → cancel the
stop, close reduce-only (IOC, then MARKET if needed) → the position must be zero. Each step
is timed and recorded; any failure is visible on the status page.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from quanta.oms.oms import Intent
from quanta.oms.state import OrderStatus
from quanta.trader.service import S, market_ok

if TYPE_CHECKING:
    from quanta.trader.service import Trader

DONE = {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.EXPIRED, OrderStatus.REJECTED}


async def close_position(t: Trader, symbol: str, key: str) -> bool:
    """Reduce-only close: an IOC just through the book, then MARKET for any remainder."""
    rules = t.rules[symbol]
    for attempt in range(2):
        pos = t.store.positions().get(symbol)
        if not pos:
            return True
        side = "SELL" if pos > 0 else "BUY"
        mkt = t.market.view(symbol)
        if attempt == 0:
            px = (
                rules.price(mkt.bid * 0.998, "down")
                if side == "SELL"
                else rules.price(mkt.ask * 1.002, "up")
            )
            intent = Intent(
                "exit",
                symbol,
                side,
                "LIMIT",
                rules.qty(abs(pos)),
                px,
                "IOC",
                reduce_only=True,
                key=f"{key}-x{attempt}",
            )
        else:
            intent = Intent(
                "exit",
                symbol,
                side,
                "MARKET",
                rules.qty(abs(pos), market=True),
                reduce_only=True,
                key=f"{key}-x{attempt}",
            )
        r = await t.place(intent)
        if r.client_id:
            await t.wait_status(r.client_id, DONE, 5.0)
        await asyncio.sleep(0.5)  # let the fill events arrive
    return not t.store.positions().get(symbol)


async def canary_cycle(t: Trader, n: int) -> dict[str, Any]:
    cfg = t.cfg.canary
    sym = cfg.symbol
    out: dict[str, Any] = {"ts_ns": t.clock.now_ns(), "n": n, "symbol": sym}
    mkt = t.market.view(sym)
    if not t.ready or not t.kill.allows_entry():
        return out | {"result": "skipped", "why": f"seviye {t.kill.level.name}"}
    if t.store.positions().get(sym) or not market_ok(mkt):
        return out | {"result": "skipped", "why": "pozisyon açık ya da piyasa verisi yok"}
    rules = t.rules[sym]
    px = rules.price(mkt.bid, "down")
    qty = rules.qty_for_notional(float(rules.min_notional) * cfg.notional_mult, float(px))
    key = f"canary-{t.store.epoch}-{n}"
    r = await t.place(Intent("entry", sym, "BUY", "LIMIT", qty, px, "GTX", key=f"{key}-entry"))
    if not r.ok or r.client_id is None:
        return out | {"result": "rejected", "why": r.error}
    out["ack_ms"] = t.oms.last_ack_ms
    status = await t.wait_status(r.client_id, {OrderStatus.FILLED} | DONE, cfg.entry_wait_s)
    if status is not OrderStatus.FILLED:
        await t.oms.cancel(r.client_id)
        status = await t.wait_status(r.client_id, DONE, 5.0)
    row = t.store.order(r.client_id)
    await asyncio.sleep(0.3)
    pos = t.store.positions().get(sym)
    if not pos:
        return out | {"result": "not_filled", "status": status.value}
    out["entry_price"] = str(row.avg_price if row else px)
    trig = rules.price(float(row.avg_price if row else px) * (1 - cfg.stop_pct), "down")
    stop = await t.place(
        Intent(
            "protect",
            sym,
            "SELL",
            "STOP_MARKET",
            trigger=trig,
            close_position=True,
            key=f"{key}-stop",
        )
    )
    out["stop"] = "ok" if stop.ok else f"hata: {stop.error}"
    deadline = t.clock.now_ns() + int(cfg.hold_s * S)
    while t.clock.now_ns() < deadline and t.kill.level.value < 3:
        await asyncio.sleep(0.5)
        if not t.store.positions().get(sym):
            break  # the protective stop (or a flatten) closed it
    if stop.client_id:
        await t.oms.cancel(stop.client_id)
    closed = await close_position(t, sym, key)
    fills = t.store.fills_since(out["ts_ns"] // 1_000_000)
    out["pnl_usdt"] = float(sum(float(f["realized"]) - float(f["fee"]) for f in fills))
    out["fills"] = len(fills)
    out["result"] = "ok" if closed and stop.ok else "error"
    if not closed:
        out["why"] = "pozisyon kapanmadı"
        t._problem("canary", "pozisyon kapanmadı")
    return out


async def canary_loop(t: Trader, stop: asyncio.Event) -> None:
    n = 0
    while not stop.is_set():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), 5.0 if not n else t.cfg.canary.every_s)
        if stop.is_set():
            break
        n += 1
        try:
            res = await canary_cycle(t, n)
        except Exception as e:
            res = {"ts_ns": t.clock.now_ns(), "n": n, "result": "error", "why": repr(e)[:200]}
        t.canary.append(res)
        t.store.journal(
            t.clock.now_ns(), "canary", **{k: v for k, v in res.items() if k != "ts_ns"}
        )
