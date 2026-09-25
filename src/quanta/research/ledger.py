"""Trial ledger: every evaluated configuration is recorded (append-only JSON lines in the
repository), so the number of trials behind a result is never forgotten — the Deflated
Sharpe Ratio is only honest if all tries are counted (plan §9). Daily returns of each trial
are kept next to it (gzip CSV) so later analyses can re-estimate trial correlations.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

LEDGER = Path("research/ledger/trials.jsonl")
RETURNS = Path("research/ledger/returns")


def trial_id(
    hypothesis: str, version: int, params: dict[str, Any], data_sha: str, period: str
) -> str:
    key = json.dumps([hypothesis, version, params, data_sha, period], sort_keys=True, default=str)
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def returns_csv(days: NDArray[np.int64], ret: NDArray[np.float64]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["date", "ret"])
    for d, r in zip(days, ret, strict=True):
        w.writerow([datetime.fromtimestamp(int(d) * 86400, UTC).date().isoformat(), f"{r:.10g}"])
    return gzip.compress(buf.getvalue().encode(), mtime=0)


@dataclass(slots=True)
class Ledger:
    repo: Path

    @property
    def path(self) -> Path:
        return self.repo / LEDGER

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def _append(self, entry: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as fh:
            fh.write(json.dumps(entry, sort_keys=True, default=str) + "\n")

    def record_trial(
        self,
        base: dict[str, Any],
        tid: str,
        params: dict[str, Any],
        days: NDArray[np.int64],
        ret: NDArray[np.float64],
        stats: dict[str, float],
    ) -> bool:
        """Append a trial once (by id); returns False when it was already recorded."""
        if any(e.get("trial_id") == tid and e.get("kind") == "trial" for e in self.entries()):
            return False
        data = returns_csv(days, ret)
        rel = RETURNS / f"{tid}.csv.gz"
        (self.repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / rel).write_bytes(data)
        self._append(
            base
            | {
                "kind": "trial",
                "trial_id": tid,
                "params": params,
                "returns_file": str(rel),
                "returns_sha256": hashlib.sha256(data).hexdigest(),
                **stats,
            }
        )
        return True

    def trials_of(self, hypotheses: list[str], period: str, data_sha: str) -> list[dict[str, Any]]:
        """Counted (non-exploratory) trials of ``hypotheses`` on the same period and data."""
        return [
            e
            for e in self.entries()
            if e.get("kind") == "trial"
            and e.get("hypothesis") in hypotheses
            and not e.get("exploratory")
            and e.get("period") == period
            and e.get("data_sha") == data_sha
        ]

    def load_returns(self, entry: dict[str, Any]) -> tuple[NDArray[np.int64], NDArray[np.float64]]:
        """Daily returns of a recorded trial; the file must match its recorded sha256."""
        data = (self.repo / entry["returns_file"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["returns_sha256"]:
            raise ValueError(f"{entry['returns_file']}: sha256 differs from the ledger")
        rows = list(csv.reader(io.StringIO(gzip.decompress(data).decode())))[1:]
        epoch = date(1970, 1, 1).toordinal()
        days = np.array([date.fromisoformat(d).toordinal() - epoch for d, _ in rows], np.int64)
        return days, np.array([float(r) for _, r in rows], dtype=np.float64)

    def lockbox_opened(self, hypothesis: str) -> bool:
        return any(
            e.get("kind") == "lockbox_opened" and e.get("hypothesis") == hypothesis
            for e in self.entries()
        )

    def open_lockbox(self, base: dict[str, Any]) -> None:
        if self.lockbox_opened(base["hypothesis"]):
            raise RuntimeError(f"{base['hypothesis']}: the lockbox was already opened once")
        self._append(base | {"kind": "lockbox_opened"})

    def count(self, hypothesis: str | None = None, include_exploratory: bool = False) -> int:
        return sum(
            1
            for e in self.entries()
            if e.get("kind") == "trial"
            and (hypothesis is None or e.get("hypothesis") == hypothesis)
            and (include_exploratory or not e.get("exploratory"))
        )
