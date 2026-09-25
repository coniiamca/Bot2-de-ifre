"""Kill switch levels (design §13.2): ACTIVE → PAUSED → REDUCING → HALTED → FLATTENING.

The effective level is the highest of

* a **sticky** level, set by limits (daily loss, drawdown) or a person, persisted across
  restarts; HALTED/FLATTENING are left **only by a person** (``resume``);
* **conditions** that clear themselves when the cause goes away: stale market data, the user
  stream down, an order with an unknown outcome, clock offset, a reconciliation mismatch.

New entries are allowed only when the level is ACTIVE; reduce-only exits are always allowed.
"""

from __future__ import annotations

from enum import IntEnum

from quanta.core.clock import Clock
from quanta.oms.store import Store


class Level(IntEnum):
    ACTIVE = 0
    PAUSED = 1
    REDUCING = 2
    HALTED = 3
    FLATTENING = 4


TR = {
    Level.ACTIVE: "AKTİF",
    Level.PAUSED: "DURAKLATILDI",
    Level.REDUCING: "YALNIZ AZALTMA",
    Level.HALTED: "DURDURULDU",
    Level.FLATTENING: "KAPATILIYOR",
}


class KillSwitch:
    def __init__(self, store: Store, clock: Clock) -> None:
        self.store = store
        self.clock = clock
        self.sticky = Level(int(store.get("kill_level", "0") or 0))
        self.sticky_reason = store.get("kill_reason", "") or ""
        self.conditions: dict[str, tuple[Level, str]] = {}

    @property
    def level(self) -> Level:
        return max([self.sticky, *(lv for lv, _ in self.conditions.values())])

    @property
    def reasons(self) -> list[str]:
        out = [self.sticky_reason] if self.sticky > Level.ACTIVE and self.sticky_reason else []
        return out + [f"{name}: {detail}" for name, (_, detail) in sorted(self.conditions.items())]

    def allows_entry(self) -> bool:
        return self.level == Level.ACTIVE

    def condition(self, name: str, level: Level | None, detail: str = "") -> None:
        """Set (level) or clear (None) an automatic condition."""
        before = self.conditions.get(name)
        if level is None:
            if before is not None:
                del self.conditions[name]
                self.store.journal(self.clock.now_ns(), "condition_cleared", name=name)
            return
        if before is None or before[0] != level:
            self.store.journal(
                self.clock.now_ns(), "condition", name=name, level=level.name, detail=detail
            )
        self.conditions[name] = (level, detail)

    def escalate(self, level: Level, reason: str, by: str = "system") -> None:
        if level <= self.sticky:
            return
        self.sticky, self.sticky_reason = level, reason
        self._save(by)

    def set_sticky(self, level: Level, reason: str, by: str) -> None:
        """Set the persisted level directly (e.g. FLATTENING → HALTED after a flatten)."""
        self.sticky, self.sticky_reason = level, reason
        self._save(by)

    def resume(self, by: str) -> None:
        """Back to ACTIVE — the only way out of HALTED; a person's command (or a demo drill)."""
        self.sticky, self.sticky_reason = Level.ACTIVE, ""
        self._save(by)

    def _save(self, by: str) -> None:
        self.store.set("kill_level", str(int(self.sticky)))
        self.store.set("kill_reason", self.sticky_reason)
        self.store.journal(
            self.clock.now_ns(),
            "kill_level",
            level=self.sticky.name,
            reason=self.sticky_reason,
            by=by,
        )
