"""Property tests for the Binance depth sequencing algorithm.

Model: an exchange emits a stream of diff events for one symbol. Each event changes some
price levels (absolute quantities) and carries update ids U..u with pu = previous u; ids
may skip (Binance ids are not contiguous per symbol). A snapshot is the true book state
after some event k (lastUpdateId = u_k). Delivery to the client may duplicate events and
may drop events.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

from hypothesis import given, settings
from hypothesis import strategies as st

from quanta.venues.binance_usdm.sequencing import DepthSequencer, SyncState


@dataclass(frozen=True)
class Ev:
    U: int
    u: int
    pu: int
    changes: tuple[tuple[int, int], ...]  # (price, qty) ; qty 0 = delete


@st.composite
def event_streams(draw: st.DrawFn) -> list[Ev]:
    n = draw(st.integers(min_value=3, max_value=60))
    events: list[Ev] = []
    last_u = draw(st.integers(min_value=1, max_value=10_000))
    for _ in range(n):
        U = last_u + draw(st.integers(min_value=1, max_value=5))
        u = U + draw(st.integers(min_value=0, max_value=5))
        changes = tuple(
            draw(st.lists(st.tuples(st.integers(0, 20), st.integers(0, 3)), min_size=1, max_size=4))
        )
        events.append(Ev(U, u, last_u, changes))
        last_u = u
    return events


def apply(book: dict[int, int], ev: Ev) -> None:
    for price, qty in ev.changes:
        if qty == 0:
            book.pop(price, None)
        else:
            book[price] = qty


def true_state_after(events: list[Ev], k: int) -> dict[int, int]:
    book: dict[int, int] = {}
    for ev in events[: k + 1]:
        apply(book, ev)
    return book


@settings(max_examples=300, deadline=None)
@given(events=event_streams(), data=st.data())
def test_duplicates_only_reconstructs_exact_book(events: list[Ev], data: st.DataObject) -> None:
    k = data.draw(st.integers(0, len(events) - 1), label="snapshot_after")
    # buffer point: the snapshot is fetched after `b` events were delivered (b >= k+1 so the
    # snapshot is not newer than... any buffered state; we also allow b <= k: stale events
    # are dropped, the bridging one arrives later)
    b = data.draw(st.integers(0, len(events)), label="delivered_before_snapshot")
    dup_idx = data.draw(st.lists(st.integers(0, len(events) - 1), max_size=10), label="dups")
    delivery = list(events)
    for i in sorted(dup_idx, reverse=True):
        delivery.insert(i + 1, events[i])  # re-deliver event i right after itself
    seq: DepthSequencer[Ev] = DepthSequencer()
    book: dict[int, int] = {}
    applied: list[Ev] = []
    # deliver the first b events (by position in the original order), then snapshot
    first_part = [e for e in delivery if events.index(e) < b]
    rest = [e for e in delivery if events.index(e) >= b]
    for e in first_part:
        assert seq.on_event(e).apply == []  # buffering before snapshot
    res = seq.on_snapshot(events[k].u)
    book = true_state_after(events, k)
    for e in res.apply:
        apply(book, e)
        applied.append(e)
    for e in rest:
        r = seq.on_event(e)
        assert r.gap is None, "duplicates must never be reported as gaps"
        for x in r.apply:
            apply(book, x)
            applied.append(x)
    # continuity of applied events
    for prev, cur in itertools.pairwise(applied):
        assert cur.pu == prev.u
    if applied:
        assert applied[0].U <= events[k].u <= applied[0].u or applied[0].pu == events[k].u
    # book equals the true final state (absolute updates are idempotent)
    if seq.state is SyncState.LIVE:
        assert book == true_state_after(events, len(events) - 1)
    else:
        # only possible if no event after the snapshot was ever delivered
        assert k == len(events) - 1


@settings(max_examples=300, deadline=None)
@given(events=event_streams(), data=st.data())
def test_dropped_event_after_sync_is_detected(events: list[Ev], data: st.DataObject) -> None:
    if len(events) < 4:
        return
    k = data.draw(st.integers(0, len(events) - 3), label="snapshot_after")
    drop = data.draw(st.integers(k + 1, len(events) - 2), label="dropped")
    seq: DepthSequencer[Ev] = DepthSequencer()
    seq.on_snapshot(events[k].u)
    applied: list[Ev] = []
    gaps = 0
    for i, e in enumerate(events[k:], start=k):
        if i == drop:
            continue
        r = seq.on_event(e)
        applied += r.apply
        if r.gap is not None:
            gaps += 1
            assert i == drop + 1, "gap must be detected on the first event after the drop"
            break
    assert gaps == 1
    assert seq.state is SyncState.NEED_SNAPSHOT
    for prev, cur in itertools.pairwise(applied):
        assert cur.pu == prev.u


@settings(max_examples=200, deadline=None)
@given(events=event_streams(), data=st.data())
def test_stale_snapshot_requests_new_snapshot(events: list[Ev], data: st.DataObject) -> None:
    """Snapshot older than the oldest buffered event → gap, keep buffer, need new snapshot."""
    start = data.draw(st.integers(1, len(events) - 1), label="first_buffered")
    seq: DepthSequencer[Ev] = DepthSequencer()
    for e in events[start:]:
        seq.on_event(e)
    stale_id = (
        events[start - 1].u - 1
        if events[start - 1].u > events[start - 1].U
        else (events[start - 1].pu)
    )
    res = seq.on_snapshot(stale_id)
    assert res.gap is not None and res.apply == []
    assert seq.state is SyncState.NEED_SNAPSHOT
    # a fresh snapshot matching a buffered event recovers
    res2 = seq.on_snapshot(events[-1].u)
    assert res2.gap is None
    assert seq.state is SyncState.LIVE


def test_successor_event_bridges_snapshot() -> None:
    """Snapshot ends exactly on an event boundary and that event was never delivered."""
    seq: DepthSequencer[Ev] = DepthSequencer()
    seq.on_snapshot(100)
    r = seq.on_event(Ev(U=103, u=105, pu=100, changes=((1, 1),)))
    assert r.gap is None and len(r.apply) == 1
    assert seq.state is SyncState.LIVE
    # but a successor of a *different* state is a gap
    seq2: DepthSequencer[Ev] = DepthSequencer()
    seq2.on_snapshot(100)
    r2 = seq2.on_event(Ev(U=103, u=105, pu=101, changes=((1, 1),)))
    assert r2.gap is not None
