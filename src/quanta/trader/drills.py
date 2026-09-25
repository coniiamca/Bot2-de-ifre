"""Drills on the **demo** account, first 10 minutes after start and then daily:

* ``flatten``: open a small position with a protective stop, run the emergency close, check
  that nothing is left open, return to ACTIVE (only drills may do that without a person).
* ``deadman``: a resting order and a far conditional order, then let the countdown run out:
  does ``countdownCancelAll`` cancel conditional (algo) orders too? (design §12.5)
* ``reconnect``: drop the user stream, measure how long until it is back and reconciled.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from quanta.oms.oms import Intent
from quanta.oms.state import OrderStatus
from quanta.risk.killswitch import Level
from quanta.trader.service import S, market_ok

if TYPE_CHECKING:
    from quanta.trader.service import Trader


async def drill_flatten(t: Trader) -> dict[str, Any]:
    sym = t.cfg.canary.symbol
    rules = t.rules[sym]
    mkt = t.market.view(sym)
    if not t.kill.allows_entry() or t.store.positions().get(sym) or not market_ok(mkt):
        return {"result": "skipped", "why": f"seviye {t.kill.level.name} / pozisyon açık"}
    qty = rules.qty_for_notional(float(rules.min_notional) * 1.1, mkt.ask)
    r = await t.place(Intent("drill", sym, "BUY", "MARKET", qty))
    if not r.ok or r.client_id is None:
        return {"result": "error", "why": r.error}
    await t.wait_status(r.client_id, {OrderStatus.FILLED}, 5.0)
    trig = rules.price(mkt.mark * 0.98, "down")
    await t.place(Intent("protect", sym, "SELL", "STOP_MARKET", trigger=trig, close_position=True))
    await asyncio.sleep(1.0)
    t0 = t.clock.now_ns()
    flat = await t.flatten("tatbikat: acil kapatma", "drill")
    secs = (t.clock.now_ns() - t0) / S
    open_orders = await t.rest.open_orders(sym)
    open_algos = await t.rest.open_algo_orders(sym)
    ok = flat and not open_orders and not open_algos and t.kill.level >= Level.HALTED
    if t.cfg.environment == "demo" and t.kill.sticky_reason.startswith("tatbikat"):
        t.kill.resume("drill")
    return {
        "result": "ok" if ok else "error",
        "seconds": round(secs, 1),
        "flat": flat,
        "left_orders": len(open_orders),
        "left_algos": len(open_algos),
    }


async def drill_deadman(t: Trader) -> dict[str, Any]:
    sym = t.cfg.canary.symbol
    rules = t.rules[sym]
    mkt = t.market.view(sym)
    if not t.kill.allows_entry() or not market_ok(mkt):
        return {"result": "skipped", "why": f"seviye {t.kill.level.name}"}
    t.deadman_hold.add(sym)
    try:
        qty = rules.qty_for_notional(float(rules.min_notional) * 1.1, mkt.bid)
        px = rules.price(mkt.bid * 0.995, "down")
        limit = await t.place(Intent("drill", sym, "BUY", "LIMIT", qty, px, "GTX"))
        trig = rules.price(mkt.mark * 1.03, "up")
        algo = await t.place(Intent("drill", sym, "BUY", "STOP_MARKET", qty, trigger=trig))
        if not (limit.ok and algo.ok):
            return {"result": "error", "why": f"{limit.error or ''} {algo.error or ''}".strip()}
        ms = t.cfg.drills.deadman_countdown_ms
        await t.rest.countdown_cancel_all(sym, ms)
        await asyncio.sleep(ms / 1000 + 3.0)
        orders = {o["clientOrderId"] for o in await t.rest.open_orders(sym)}
        algos = {a["clientAlgoId"] for a in await t.rest.open_algo_orders(sym)}
        regular_cancelled = limit.client_id not in orders
        algo_cancelled = algo.client_id not in algos
        await t.cancel_everything(sym)
        await t.rest.countdown_cancel_all(sym, 0)
        t.store.set_json("deadman_cancels_algo", algo_cancelled)
        return {
            "result": "ok" if regular_cancelled else "error",
            "regular_cancelled": regular_cancelled,
            "algo_cancelled": algo_cancelled,
        }
    finally:
        t.deadman_hold.discard(sym)


async def drill_reconnect(t: Trader) -> dict[str, Any]:
    t0 = t.clock.now_ns()
    await t.user.force_reconnect()
    await asyncio.sleep(0.5)
    for _ in range(300):  # up to 60 s
        if t.user.connected:
            break
        await asyncio.sleep(0.2)
    await t.reconcile()
    secs = (t.clock.now_ns() - t0) / S
    return {
        "result": "ok" if t.user.connected and secs <= 60 else "error",
        "seconds": round(secs, 1),
    }


DRILLS = {"flatten": drill_flatten, "deadman": drill_deadman, "reconnect": drill_reconnect}


async def run_drills(t: Trader) -> None:
    for name, fn in DRILLS.items():
        try:
            res = await fn(t)
        except Exception as e:
            res = {"result": "error", "why": repr(e)[:200]}
        res["ts_ns"] = t.clock.now_ns()
        t.drills[name] = res
        t.store.journal(
            t.clock.now_ns(), "drill", name=name, **{k: v for k, v in res.items() if k != "ts_ns"}
        )
    t.store.set_json("drills", t.drills)


async def drill_loop(t: Trader, stop: asyncio.Event) -> None:
    t.drills = t.store.get_json("drills") or {}
    wait = t.cfg.drills.first_after_s
    while not stop.is_set():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), wait)
        if stop.is_set():
            break
        if t.ready:
            await run_drills(t)
            wait = t.cfg.drills.every_s
        else:
            wait = 60.0
