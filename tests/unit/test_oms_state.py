"""Order state machine: out-of-order and duplicate reports never corrupt an order."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from quanta.oms.state import (
    OPEN,
    TERMINAL,
    IllegalTransition,
    OrderStatus,
    advance,
)

S = OrderStatus
REPORTS = [S.NEW, S.PARTIALLY_FILLED, S.FILLED, S.CANCELED, S.EXPIRED, S.UNKNOWN]


def test_normal_paths() -> None:
    s = advance(S.PENDING, S.SENT)
    for r in (S.NEW, S.PARTIALLY_FILLED, S.PARTIALLY_FILLED, S.FILLED):
        s = advance(s, r)
    assert s is S.FILLED
    assert advance(advance(S.SENT, S.UNKNOWN), S.NEW) is S.NEW  # resolved by a query
    assert advance(S.UNKNOWN, S.REJECTED) is S.REJECTED  # the exchange never had it
    assert advance(S.SENT, S.REJECTED) is S.REJECTED


def test_stale_and_duplicate_reports_are_ignored() -> None:
    assert advance(S.PARTIALLY_FILLED, S.NEW) is S.PARTIALLY_FILLED  # REST behind the stream
    assert advance(S.FILLED, S.NEW) is S.FILLED
    assert advance(S.FILLED, S.FILLED) is S.FILLED
    assert advance(S.NEW, S.UNKNOWN) is S.NEW  # a timeout after we saw it


def test_contradictions_raise() -> None:
    with pytest.raises(IllegalTransition):
        advance(S.FILLED, S.CANCELED)
    with pytest.raises(IllegalTransition):
        advance(S.NEW, S.REJECTED)
    with pytest.raises(IllegalTransition):
        advance(S.PENDING, S.FILLED)


@given(
    st.lists(st.sampled_from(REPORTS), max_size=12), st.sampled_from(list(TERMINAL - {S.REJECTED}))
)
def test_any_report_order_ends_in_the_final_report(
    reports: list[OrderStatus], final: OrderStatus
) -> None:
    """Whatever arrives first (stream, REST, duplicates), once the exchange's final status
    is reported the order is in it, and it never leaves a terminal status."""
    s = advance(S.PENDING, S.SENT)
    for r in reports:
        try:
            nxt = advance(s, r)
        except IllegalTransition:
            assert s in TERMINAL and r in TERMINAL
            continue
        if s in TERMINAL:
            assert nxt is s
        s = nxt
    if s in OPEN or s in (S.SENT, S.UNKNOWN):
        s = advance(s, final)
        assert s is final
    assert s in TERMINAL or s in OPEN or s in (S.SENT, S.UNKNOWN)
