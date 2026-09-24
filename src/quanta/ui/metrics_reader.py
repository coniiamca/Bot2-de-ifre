"""Read the recorder's Prometheus metrics (text exposition) into queryable snapshots.

The UI runs as a separate process and only *reads* the recorder's public ``/metrics``
endpoint, so a UI bug can never affect data capture.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field

from prometheus_client.parser import text_string_to_metric_families

Labels = frozenset[tuple[str, str]]


@dataclass(slots=True)
class Snapshot:
    ts: float  # unix seconds when scraped
    samples: dict[str, dict[Labels, float]] = field(default_factory=dict)

    def series(self, name: str) -> dict[Labels, float]:
        return self.samples.get(name, {})

    def get(self, name: str, **labels: str) -> float | None:
        return self.series(name).get(frozenset(labels.items()))

    def by_label(self, name: str, label: str, **match: str) -> dict[str, float]:
        """Sum a series grouped by one label, optionally filtered by other labels."""
        out: dict[str, float] = {}
        for lbls, v in self.series(name).items():
            d = dict(lbls)
            if all(d.get(k) == val for k, val in match.items()) and label in d:
                out[d[label]] = out.get(d[label], 0.0) + v
        return out

    def total(self, name: str, **match: str) -> float:
        return sum(
            v
            for lbls, v in self.series(name).items()
            if all(dict(lbls).get(k) == val for k, val in match.items())
        )


def parse(text: str, ts: float) -> Snapshot:
    snap = Snapshot(ts)
    for family in text_string_to_metric_families(text):
        for s in family.samples:
            snap.samples.setdefault(s.name, {})[frozenset(s.labels.items())] = float(s.value)
    return snap


def histogram_quantile(q: float, buckets: Iterable[tuple[float, float]]) -> float | None:
    """Prometheus-style quantile from cumulative (upper_bound, count) buckets."""
    items = sorted(buckets)
    if not items or items[-1][1] <= 0:
        return None
    total = items[-1][1]
    rank = q * total
    prev_bound, prev_count = 0.0, 0.0
    for bound, count in items:
        if count >= rank:
            if math.isinf(bound):
                return prev_bound
            width = count - prev_count
            frac = 0.0 if width <= 0 else (rank - prev_count) / width
            return prev_bound + (bound - prev_bound) * frac
        prev_bound, prev_count = bound, count
    return prev_bound


def bucket_deltas(
    new: Snapshot, old: Snapshot | None, name: str, **match: str
) -> list[tuple[float, float]]:
    """Cumulative bucket counts accumulated between two snapshots (summed over labels)."""

    def collect(snap: Snapshot | None) -> dict[float, float]:
        acc: dict[float, float] = {}
        if snap is None:
            return acc
        for lbls, v in snap.series(name).items():
            d = dict(lbls)
            if not all(d.get(k) == val for k, val in match.items()):
                continue
            le = d.get("le")
            if le is None:
                continue
            bound = math.inf if le in ("+Inf", "inf") else float(le)
            acc[bound] = acc.get(bound, 0.0) + v
        return acc

    now, before = collect(new), collect(old)
    return [(b, max(0.0, c - before.get(b, 0.0))) for b, c in now.items()]
