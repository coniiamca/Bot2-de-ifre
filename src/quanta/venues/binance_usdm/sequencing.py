"""Sequence integrity for Binance USDⓈ-M streams (pure state machines, property-tested).

DepthSequencer implements the official local order book algorithm
(developers.binance.com … /How-to-manage-a-local-order-book-correctly):

1. Buffer diff events; fetch a REST snapshot (``lastUpdateId``).
2. Drop buffered events with ``u < lastUpdateId``.
3. The first applied event must satisfy ``U <= lastUpdateId <= u``.
4. Each subsequent event must have ``pu == previous u``; otherwise re-snapshot.

Extensions (each provably safe):

* Events with ``u <= last applied u`` are duplicates (make-before-break rotation, redundant
  connections) and are dropped, never treated as gaps.
* An event with ``pu == lastUpdateId`` is also accepted as the first event: it is by
  definition the immediate successor of the snapshot state (the documented rule would
  force an unnecessary re-snapshot when the snapshot ends exactly on an event boundary).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class HasUpdateIds(Protocol):
    @property
    def U(self) -> int: ...  # exchange field names
    @property
    def u(self) -> int: ...
    @property
    def pu(self) -> int: ...


class SyncState(Enum):
    NEED_SNAPSHOT = "need_snapshot"  # buffering; caller must fetch a snapshot
    AWAIT_FIRST = "await_first"  # snapshot applied; waiting for the bridging event
    LIVE = "live"


@dataclass(frozen=True, slots=True)
class Gap:
    expected_pu: int
    got_U: int
    got_u: int
    got_pu: int
    reason: str


@dataclass(slots=True)
class StepResult[E: HasUpdateIds]:
    apply: list[E]
    gap: Gap | None = None
    duplicates: int = 0
    stale: int = 0


class DepthSequencer[E: HasUpdateIds]:
    def __init__(self, max_buffer: int = 20_000) -> None:
        self.state = SyncState.NEED_SNAPSHOT
        self.last_u: int | None = None
        self.snapshot_id: int | None = None
        self._buffer: deque[E] = deque(maxlen=max_buffer)
        self.buffer_overflowed = False

    @property
    def buffered(self) -> int:
        return len(self._buffer)

    def on_event(self, ev: E) -> StepResult[E]:
        if self.state is SyncState.NEED_SNAPSHOT:
            if len(self._buffer) == self._buffer.maxlen:
                self.buffer_overflowed = True
            self._buffer.append(ev)
            return StepResult(apply=[])

        if self.state is SyncState.AWAIT_FIRST:
            assert self.snapshot_id is not None
            sid = self.snapshot_id
            if ev.u < sid:
                return StepResult(apply=[], stale=1)
            if ev.U <= sid <= ev.u or ev.pu == sid:
                self.state = SyncState.LIVE
                self.last_u = ev.u
                return StepResult(apply=[ev])
            gap = Gap(sid, ev.U, ev.u, ev.pu, "snapshot_older_than_stream")
            self._restart(ev)
            return StepResult(apply=[], gap=gap)

        # LIVE
        assert self.last_u is not None
        if ev.u <= self.last_u:
            return StepResult(apply=[], duplicates=1)
        if ev.pu == self.last_u:
            self.last_u = ev.u
            return StepResult(apply=[ev])
        gap = Gap(self.last_u, ev.U, ev.u, ev.pu, "pu_mismatch")
        self._restart(ev)
        return StepResult(apply=[], gap=gap)

    def on_snapshot(self, last_update_id: int) -> StepResult[E]:
        """Apply a REST snapshot. Returns buffered events to apply after the snapshot."""
        if self.state is not SyncState.NEED_SNAPSHOT:
            # Late/unsolicited snapshot (e.g. an audit snapshot) — ignore for sequencing.
            return StepResult(apply=[])
        buffered = list(self._buffer)
        self._buffer.clear()
        self.buffer_overflowed = False
        self.snapshot_id = last_update_id
        self.state = SyncState.AWAIT_FIRST
        self.last_u = None
        result: StepResult[E] = StepResult(apply=[])
        for ev in buffered:
            step = self.on_event(ev)
            result.apply.extend(step.apply)
            result.duplicates += step.duplicates
            result.stale += step.stale
            if step.gap is not None:
                # Snapshot predates the oldest event we hold: fetch a newer snapshot.
                # _restart() already re-buffered the offending event; keep the rest too.
                idx = buffered.index(ev)
                for rest in buffered[idx + 1 :]:
                    self._buffer.append(rest)
                result.gap = step.gap
                result.apply.clear()
                return result
        return result

    def reset(self) -> None:
        """Forget all state (e.g. after a connection loss on the only depth connection)."""
        self._buffer.clear()
        self.state = SyncState.NEED_SNAPSHOT
        self.last_u = None
        self.snapshot_id = None

    def _restart(self, ev: E) -> None:
        self._buffer.clear()
        self._buffer.append(ev)
        self.state = SyncState.NEED_SNAPSHOT
        self.last_u = None
        self.snapshot_id = None


@dataclass(frozen=True, slots=True)
class IdGap:
    first_missing: int
    last_missing: int

    @property
    def count(self) -> int:
        return self.last_missing - self.first_missing + 1


class ContiguousIdTracker:
    """Tracks a strictly increasing, contiguous id sequence (e.g. aggTrade ``a``)."""

    __slots__ = ("duplicates", "gaps_total", "last_id", "missing_total")

    def __init__(self) -> None:
        self.last_id: int | None = None
        self.duplicates = 0
        self.gaps_total = 0
        self.missing_total = 0

    def observe(self, ident: int) -> IdGap | None:
        """Returns the gap if ids were skipped; duplicates/out-of-date ids are counted."""
        last = self.last_id
        if last is None:
            self.last_id = ident
            return None
        if ident <= last:
            self.duplicates += 1
            return None
        self.last_id = ident
        if ident == last + 1:
            return None
        gap = IdGap(last + 1, ident - 1)
        self.gaps_total += 1
        self.missing_total += gap.count
        return gap
