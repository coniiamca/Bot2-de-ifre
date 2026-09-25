"""What an error from the order API means for the order (plan §12.3; Binance general-info
and error-code pages). The decisive question is whether the order *may have executed*:

* ``UNKNOWN`` — it may have: HTTP 503 "Unknown error", -1007 (backend timeout), other 5xx,
  or no response at all. Never resend: freeze the symbol and query by clientOrderId.
* ``REJECTED`` — it did not execute (4xx with an error code, e.g. -2019 margin, -5022 post-
  only would take, -2021 would trigger immediately, -4116 duplicate id, -4120 wrong endpoint).
* ``RETRY_LATER`` — not executed because of load or rate limits (429/418, -1008, 503
  "Service Unavailable"/"Internal error … try again").
* ``CLOCK`` — not executed, timestamp outside recvWindow (-1021, -5028): resync the clock.
* ``AUTH`` — key, signature or permission problem (-1022, -2014, -2015): stop trading.
* ``NOT_FOUND`` — query/cancel of an order the exchange does not know (-2011, -2013).
"""

from __future__ import annotations

from enum import StrEnum


class Action(StrEnum):
    UNKNOWN = "unknown"
    REJECTED = "rejected"
    RETRY_LATER = "retry_later"
    CLOCK = "clock"
    AUTH = "auth"
    NOT_FOUND = "not_found"


RETRY_CODES = {-1003, -1008, -1015}
CLOCK_CODES = {-1021, -5028}
AUTH_CODES = {-1002, -1022, -2014, -2015}
NOT_FOUND_CODES = {-2011, -2013}
UNKNOWN_CODES = {-1006, -1007}


def classify(status: int, code: int | None, msg: str = "") -> Action:
    if code in UNKNOWN_CODES:
        return Action.UNKNOWN
    if status in (418, 429) or code in RETRY_CODES:
        return Action.RETRY_LATER
    if code in CLOCK_CODES:
        return Action.CLOCK
    if code in AUTH_CODES or status == 401:
        return Action.AUTH
    if code in NOT_FOUND_CODES:
        return Action.NOT_FOUND
    if status == 503:
        low = msg.lower()
        if "service unavailable" in low or "internal error" in low:
            return Action.RETRY_LATER  # documented as a failure: not executed
        return Action.UNKNOWN  # "Unknown error": execution status unknown
    if status >= 500:
        return Action.UNKNOWN
    return Action.REJECTED
