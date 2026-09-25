"""Order state machine (design §12.3). All status changes go through :func:`advance`.

Local statuses::

    PENDING → SENT → NEW → PARTIALLY_FILLED → FILLED
                 ↘ UNKNOWN ↗            ↘ CANCELED | EXPIRED
                 ↘ REJECTED

* ``PENDING``: the intent is on disk, nothing sent yet (write-ahead).
* ``SENT``: the request is in flight.
* ``UNKNOWN``: the exchange may or may not have the order (timeout, 503 "unknown"); resolved
  only by querying it — never by sending it again.
* Reports can arrive out of order (the user stream is often faster than the REST answer):
  a report that is *behind* the current status is ignored, terminal statuses never change.
"""

from __future__ import annotations

from enum import StrEnum


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    UNKNOWN = "UNKNOWN"
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


TERMINAL = frozenset(
    {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.EXPIRED, OrderStatus.REJECTED}
)
OPEN = frozenset({OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED})
RANK = {
    OrderStatus.PENDING: 0,
    OrderStatus.SENT: 1,
    OrderStatus.UNKNOWN: 1,
    OrderStatus.NEW: 2,
    OrderStatus.PARTIALLY_FILLED: 3,
    OrderStatus.FILLED: 4,
    OrderStatus.CANCELED: 4,
    OrderStatus.EXPIRED: 4,
    OrderStatus.REJECTED: 4,
}
ALLOWED: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING: frozenset({OrderStatus.SENT, OrderStatus.REJECTED}),
    OrderStatus.SENT: frozenset(OrderStatus) - {OrderStatus.PENDING, OrderStatus.SENT},
    OrderStatus.UNKNOWN: frozenset(OPEN | TERMINAL),
    OrderStatus.NEW: frozenset({OrderStatus.PARTIALLY_FILLED} | TERMINAL) - {OrderStatus.REJECTED},
    OrderStatus.PARTIALLY_FILLED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.EXPIRED,
        }
    ),
}

# exchange order / algo statuses → local status
EXCHANGE = {
    "NEW": OrderStatus.NEW,
    "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
    "FILLED": OrderStatus.FILLED,
    "CANCELED": OrderStatus.CANCELED,
    "EXPIRED": OrderStatus.EXPIRED,
    "EXPIRED_IN_MATCH": OrderStatus.EXPIRED,
    "REJECTED": OrderStatus.REJECTED,
    # algo (conditional) orders: still armed / done
    "TRIGGERING": OrderStatus.NEW,
    "TRIGGERED": OrderStatus.FILLED,
    "FINISHED": OrderStatus.FILLED,
}


class IllegalTransition(RuntimeError):
    pass


def advance(current: OrderStatus, reported: OrderStatus) -> OrderStatus:
    """The status after a report. Stale or duplicate reports leave it unchanged; a report
    that contradicts a terminal status raises (it would be a bug or an exchange anomaly)."""
    if reported == current:
        return current
    if current in TERMINAL:
        if reported in TERMINAL:
            raise IllegalTransition(f"{current} → {reported}")
        return current  # late report of an earlier state
    if RANK[reported] < RANK[current] and reported is not OrderStatus.UNKNOWN:
        return current  # behind: e.g. REST "NEW" after the stream said PARTIALLY_FILLED
    if reported is OrderStatus.UNKNOWN and current in OPEN:
        return current  # we already know it exists
    if reported not in ALLOWED.get(current, frozenset()):
        raise IllegalTransition(f"{current} → {reported}")
    return reported
