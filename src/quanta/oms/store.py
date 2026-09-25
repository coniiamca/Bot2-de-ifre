"""Durable trading state in SQLite (ADR-012): intents, orders, fills, an audit journal and
small key/value state (kill switch, daily equity). WAL mode with ``synchronous=FULL``: a row
is on disk before the call returns, so an intent is recorded *before* its order is sent.

One writer per database, enforced with an exclusive ``flock`` on ``<db>.lock``; every start
increments an epoch that is part of every clientOrderId (ids never repeat across restarts).
"""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanta.oms.state import OPEN, TERMINAL, OrderStatus, advance

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS intents(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT,                 -- caller's idempotency key (duplicate protection)
    created_ns INTEGER NOT NULL,
    purpose TEXT NOT NULL,    -- entry | exit | protect | flatten | drill
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    tif TEXT,
    qty TEXT,
    price TEXT,
    trigger TEXT,
    reduce_only INTEGER NOT NULL DEFAULT 0,
    algo INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS intents_key ON intents(key) WHERE key IS NOT NULL;
CREATE TABLE IF NOT EXISTS orders(
    client_id TEXT PRIMARY KEY,
    intent_id INTEGER,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    purpose TEXT NOT NULL,
    algo INTEGER NOT NULL DEFAULT 0,
    qty TEXT,
    price TEXT,
    status TEXT NOT NULL,
    exchange_id TEXT,
    filled TEXT NOT NULL DEFAULT '0',
    avg_price TEXT NOT NULL DEFAULT '0',
    created_ns INTEGER NOT NULL,
    updated_ns INTEGER NOT NULL,
    sent_ns INTEGER,
    ack_ns INTEGER,
    error TEXT
);
CREATE TABLE IF NOT EXISTS fills(
    symbol TEXT NOT NULL,
    trade_id INTEGER NOT NULL,
    client_id TEXT,
    side TEXT NOT NULL,
    qty TEXT NOT NULL,
    price TEXT NOT NULL,
    fee TEXT NOT NULL,
    realized TEXT NOT NULL,
    ts_ms INTEGER NOT NULL,
    PRIMARY KEY(symbol, trade_id)
);
CREATE TABLE IF NOT EXISTS journal(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ns INTEGER NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL
);
"""


class WriterLockError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OrderRow:
    client_id: str
    intent_id: int | None
    symbol: str
    side: str
    order_type: str
    purpose: str
    algo: bool
    qty: Decimal
    price: Decimal
    status: OrderStatus
    filled: Decimal
    avg_price: Decimal
    created_ns: int
    updated_ns: int
    sent_ns: int | None
    ack_ns: int | None
    error: str | None


def _row(r: sqlite3.Row) -> OrderRow:
    return OrderRow(
        r["client_id"],
        r["intent_id"],
        r["symbol"],
        r["side"],
        r["order_type"],
        r["purpose"],
        bool(r["algo"]),
        Decimal(r["qty"] or "0"),
        Decimal(r["price"] or "0"),
        OrderStatus(r["status"]),
        Decimal(r["filled"]),
        Decimal(r["avg_price"]),
        r["created_ns"],
        r["updated_ns"],
        r["sent_ns"],
        r["ack_ns"],
        r["error"],
    )


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock_fh = open(f"{path}.lock", "w")  # noqa: SIM115 — held for the lifetime
        try:
            fcntl.flock(self._lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lock_fh.close()
            raise WriterLockError(f"another trader already uses {path}") from exc
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(SCHEMA)
        os.chmod(path, 0o600)
        self.epoch = int(self.get("epoch") or 0) + 1
        self.set("epoch", str(self.epoch))

    def close(self) -> None:
        self.db.close()
        fcntl.flock(self._lock_fh, fcntl.LOCK_UN)
        self._lock_fh.close()

    # -- key/value -----------------------------------------------------------------------
    def get(self, key: str, default: str | None = None) -> str | None:
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def get_json(self, key: str) -> Any:
        v = self.get(key)
        return json.loads(v) if v else None

    def set_json(self, key: str, value: Any) -> None:
        self.set(key, json.dumps(value, sort_keys=True, default=str))

    def journal(self, ts_ns: int, kind: str, **detail: Any) -> None:
        self.db.execute(
            "INSERT INTO journal(ts_ns, kind, detail) VALUES(?, ?, ?)",
            (ts_ns, kind, json.dumps(detail, sort_keys=True, default=str)),
        )

    def recent_journal(self, n: int = 50) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM journal ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [{"ts_ns": r["ts_ns"], "kind": r["kind"], **json.loads(r["detail"])} for r in rows]

    # -- intents and orders ----------------------------------------------------------------
    def intent_exists(self, key: str) -> bool:
        return self.db.execute("SELECT 1 FROM intents WHERE key=?", (key,)).fetchone() is not None

    def add_intent(
        self,
        ts_ns: int,
        key: str | None,
        purpose: str,
        symbol: str,
        side: str,
        order_type: str,
        tif: str | None,
        qty: str | None,
        price: str | None,
        trigger: str | None,
        reduce_only: bool,
        algo: bool,
    ) -> int:
        cur = self.db.execute(
            "INSERT INTO intents(key, created_ns, purpose, symbol, side, order_type, tif, qty, "
            "price, trigger, reduce_only, algo) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                key,
                ts_ns,
                purpose,
                symbol,
                side,
                order_type,
                tif,
                qty,
                price,
                trigger,
                int(reduce_only),
                int(algo),
            ),
        )
        return int(cur.lastrowid or 0)

    def add_order(
        self,
        ts_ns: int,
        client_id: str,
        intent_id: int | None,
        symbol: str,
        side: str,
        order_type: str,
        purpose: str,
        algo: bool,
        qty: str | None,
        price: str | None,
        status: OrderStatus = OrderStatus.PENDING,
    ) -> None:
        self.db.execute(
            "INSERT INTO orders(client_id, intent_id, symbol, side, order_type, purpose, algo, "
            "qty, price, status, created_ns, updated_ns) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                client_id,
                intent_id,
                symbol,
                side,
                order_type,
                purpose,
                int(algo),
                qty,
                price,
                status.value,
                ts_ns,
                ts_ns,
            ),
        )

    def order(self, client_id: str) -> OrderRow | None:
        r = self.db.execute("SELECT * FROM orders WHERE client_id=?", (client_id,)).fetchone()
        return _row(r) if r else None

    def update_order(
        self,
        ts_ns: int,
        client_id: str,
        reported: OrderStatus,
        *,
        filled: Decimal | None = None,
        avg_price: Decimal | None = None,
        exchange_id: str | None = None,
        error: str | None = None,
        sent: bool = False,
        ack: bool = False,
    ) -> OrderRow | None:
        """Apply a report through the state machine; returns the updated row."""
        row = self.order(client_id)
        if row is None:
            return None
        status = advance(row.status, reported)
        sets = ["status=?", "updated_ns=?"]
        args: list[Any] = [status.value, ts_ns]
        if filled is not None and filled >= row.filled:
            sets.append("filled=?")
            args.append(str(filled))
        if avg_price is not None and avg_price > 0:
            sets.append("avg_price=?")
            args.append(str(avg_price))
        if exchange_id is not None:
            sets.append("exchange_id=?")
            args.append(exchange_id)
        if error is not None:
            sets.append("error=?")
            args.append(error[:300])
        if sent:
            sets.append("sent_ns=?")
            args.append(ts_ns)
        if ack and row.ack_ns is None:
            sets.append("ack_ns=?")
            args.append(ts_ns)
        args.append(client_id)
        self.db.execute(f"UPDATE orders SET {', '.join(sets)} WHERE client_id=?", args)  # noqa: S608
        return self.order(client_id)

    def orders(self, statuses: frozenset[OrderStatus] | None = None) -> list[OrderRow]:
        rows = [_row(r) for r in self.db.execute("SELECT * FROM orders ORDER BY created_ns")]
        return [r for r in rows if statuses is None or r.status in statuses]

    def open_orders(self) -> list[OrderRow]:
        return self.orders(OPEN)

    def unresolved(self) -> list[OrderRow]:
        return self.orders(frozenset({OrderStatus.SENT, OrderStatus.UNKNOWN}))

    def not_terminal(self) -> list[OrderRow]:
        return [o for o in self.orders() if o.status not in TERMINAL]

    # -- fills and positions -----------------------------------------------------------------
    def add_fill(
        self,
        symbol: str,
        trade_id: int,
        client_id: str,
        side: str,
        qty: Decimal,
        price: Decimal,
        fee: Decimal,
        realized: Decimal,
        ts_ms: int,
    ) -> bool:
        """Record a fill once (by symbol + trade id); False if it was already recorded."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO fills(symbol, trade_id, client_id, side, qty, price, fee, "
            "realized, ts_ms) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                symbol,
                trade_id,
                client_id,
                side,
                str(qty),
                str(price),
                str(fee),
                str(realized),
                ts_ms,
            ),
        )
        return cur.rowcount == 1

    def positions(self) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for r in self.db.execute("SELECT symbol, side, qty FROM fills"):
            q = Decimal(r["qty"])
            out[r["symbol"]] = out.get(r["symbol"], Decimal(0)) + (q if r["side"] == "BUY" else -q)
        adj = self.get_json("position_adjust") or {}
        for s, a in adj.items():
            out[s] = out.get(s, Decimal(0)) + Decimal(a)
        return {s: q for s, q in out.items() if q != 0}

    def adjust_position(self, symbol: str, delta: Decimal) -> None:
        """Book a difference found by reconciliation (the exchange is the truth)."""
        adj = self.get_json("position_adjust") or {}
        adj[symbol] = str(Decimal(adj.get(symbol, "0")) + delta)
        self.set_json("position_adjust", adj)

    def fills_since(self, ts_ms: int) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.db.execute("SELECT * FROM fills WHERE ts_ms>=? ORDER BY ts_ms", (ts_ms,))
        ]
