"""Compact in-memory history of derived points (24 h at the scrape interval).

Full snapshots are large; only the handful of numbers the page and the health rules need
are kept per scrape, plus cumulative counters so any window's increase can be computed.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from quanta.ui.metrics_reader import Snapshot

COUNTERS = {
    "messages": "quanta_recorder_messages_total",
    "depth_gaps": "quanta_recorder_depth_gaps_total",
    "trade_missing": "quanta_recorder_trade_missing_ids_total",
    "parse_errors": "quanta_recorder_parse_errors_total",
    "liquidations": "quanta_recorder_liquidations_total",
    "reconnects": "quanta_recorder_ws_lifecycle_total",
}


@dataclass(slots=True)
class Point:
    ts: float
    counters: dict[str, dict[str, float]] = field(default_factory=dict)  # name → venue → value
    streams_down: frozenset[str] = frozenset()
    books_unsynced: frozenset[str] = frozenset()  # "venue:symbol"


def derive(snap: Snapshot) -> Point:
    p = Point(snap.ts)
    for key, metric in COUNTERS.items():
        if key == "reconnects":
            p.counters[key] = snap.by_label(metric, "venue", event="disconnected")
        else:
            p.counters[key] = snap.by_label(metric, "venue")
    p.streams_down = frozenset(
        dict(lbls)["stream"]
        for lbls, v in snap.series("quanta_recorder_connection_up").items()
        if v < 1
    )
    p.books_unsynced = frozenset(
        f"{dict(lbls)['venue']}:{dict(lbls)['symbol']}"
        for lbls, v in snap.series("quanta_recorder_depth_synced").items()
        if v < 1
    )
    return p


class History:
    def __init__(self, max_age_s: float = 86_400) -> None:
        self.points: deque[Point] = deque()
        self.snapshots: deque[Snapshot] = deque()  # full snapshots, last ~15 min only
        self.max_age_s = max_age_s

    def add(self, snap: Snapshot) -> None:
        self.points.append(derive(snap))
        self.snapshots.append(snap)
        while self.points and snap.ts - self.points[0].ts > self.max_age_s:
            self.points.popleft()
        while self.snapshots and snap.ts - self.snapshots[0].ts > 900:
            self.snapshots.popleft()

    @property
    def latest(self) -> Snapshot | None:
        return self.snapshots[-1] if self.snapshots else None

    def snapshot_ago(self, seconds: float) -> Snapshot | None:
        """The oldest kept snapshot not older than ``seconds`` before the latest one."""
        if not self.snapshots:
            return None
        target = self.snapshots[-1].ts - seconds
        for s in self.snapshots:
            if s.ts >= target:
                return s if s is not self.snapshots[-1] else None
        return None

    def increase(self, counter: str, venue: str | None, window_s: float) -> float:
        """Counter increase over the window (resets — e.g. recorder restart — handled)."""
        if not self.points:
            return 0.0
        end = self.points[-1].ts
        total = 0.0
        prev: float | None = None
        for p in self.points:
            if p.ts < end - window_s:
                prev = _value(p, counter, venue)
                continue
            v = _value(p, counter, venue)
            if prev is not None:
                total += v - prev if v >= prev else v  # counter reset
            prev = v
        return total

    def sustained(self, seconds: float, pred: Callable[[Point], bool]) -> bool:
        """True if ``pred(point)`` held for every point in the last ``seconds`` (and the
        history covers that long)."""
        if not self.points or self.points[-1].ts - self.points[0].ts < seconds:
            return False
        end = self.points[-1].ts
        return all(pred(p) for p in self.points if p.ts >= end - seconds)

    def rate_series(
        self, counter: str, venue: str, max_points: int = 120
    ) -> list[tuple[float, float]]:
        """Per-second rate between consecutive points, downsampled to ``max_points``."""
        pts = list(self.points)
        if len(pts) < 2:
            return []
        step = max(1, (len(pts) - 1) // max_points)
        out: list[tuple[float, float]] = []
        for i in range(step, len(pts), step):
            a, b = pts[i - step], pts[i]
            dt = b.ts - a.ts
            va, vb = _value(a, counter, venue), _value(b, counter, venue)
            if dt > 0:
                out.append((b.ts, max(0.0, (vb - va if vb >= va else vb) / dt)))
        return out


def _value(p: Point, counter: str, venue: str | None) -> float:
    c = p.counters.get(counter, {})
    return sum(c.values()) if venue is None else c.get(venue, 0.0)
