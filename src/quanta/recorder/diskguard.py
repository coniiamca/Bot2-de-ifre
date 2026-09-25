"""Disk guard: stop writing capture data before the data volume fills up.

On a shared host a full disk would also take down unrelated services, and a writer that
cannot write keeps data in memory. Below ``floor_bytes`` of free space the recorder stops
writing market data (WebSocket connections stay up, so books stay in sync and recording
resumes instantly); it resumes once free space is back above ``resume_bytes``
(floor + margin, so it does not flap). Meta records are still written: both transitions
are recorded in-band, so the gap is explicit in the raw data (ADR-008).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(slots=True)
class DiskGuard:
    floor_bytes: float
    resume_bytes: float
    active: bool = False
    since_ns: int | None = None

    def update(self, free_bytes: float, now_ns: int) -> Literal["on", "off"] | None:
        """Feed a free-space measurement; returns the transition, if any."""
        if not self.active and free_bytes < self.floor_bytes:
            self.active, self.since_ns = True, now_ns
            return "on"
        if self.active and free_bytes >= self.resume_bytes:
            self.active = False
            return "off"
        return None
