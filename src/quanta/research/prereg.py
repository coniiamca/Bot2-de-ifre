"""Pre-registration: a hypothesis, its parameter grid, periods and gates are fixed in a
committed YAML file *before* results are seen. A run refuses a pre-registration that is not
committed or has local edits (unless explicitly exploratory, which never counts for gates).
"""

from __future__ import annotations

import hashlib
import itertools
import subprocess
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

# outputs a run writes itself; they may be dirty without making the run exploratory
OUTPUT_PATHS = ("research/ledger/", "docs/research/sonuclar/")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Costs(Strict):
    fee_bps: float = 5.0
    slip_top2_bps: float = 1.0
    slip_rest_bps: float = 3.0


class Validation(Strict):
    hold_days: int = 14  # purge
    lookback_days: int = 14  # embargo = lookback + 1
    cpcv_groups: int = 6
    cpcv_k: int = 2
    pbo_blocks: int = 16


class Prereg(Strict):
    id: str
    version: int
    title: str
    mechanism: str
    falsification: str
    universe_file: str = ""  # point-in-time universe CSV …
    symbols: list[str] = Field(default_factory=list)  # … or a fixed symbol list
    data_start: str  # YYYY-MM-DD, first grid hour
    dev_end: str  # exclusive; the lockbox starts here
    lockbox_end: str  # exclusive
    warmup_days: int = 15
    grid: dict[str, list[Any]]
    fixed: dict[str, Any] = Field(default_factory=dict)
    costs: Costs = Costs()
    validation: Validation = Validation()
    stress_windows: list[tuple[str, str, str]] = Field(default_factory=list)
    notes: str = ""

    def combos(self) -> list[dict[str, Any]]:
        keys = list(self.grid)
        return [
            dict(zip(keys, vals, strict=True)) for vals in itertools.product(*self.grid.values())
        ]


def _git(repo: Path, *args: str) -> str:
    # fixed program, arguments from this module only
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    ).stdout


def repo_root(path: Path) -> Path:
    out = _git(path.parent if path.is_file() else path, "rev-parse", "--show-toplevel").strip()
    return Path(out) if out else path.parent


def git_state(repo: Path) -> tuple[str, bool]:
    """(HEAD commit, whether code outside the run's own outputs has local changes)."""
    commit = _git(repo, "rev-parse", "HEAD").strip() or "unknown"
    dirty = [
        line
        for line in _git(repo, "status", "--porcelain").splitlines()
        if line[3:]
        and not line[3:].startswith(OUTPUT_PATHS)
        and not line[3:].startswith("research-data")
    ]
    return commit, bool(dirty)


class PreregError(RuntimeError):
    pass


def load_prereg(path: Path, *, require_committed: bool = True) -> tuple[Prereg, str]:
    """(model, sha256 of the file). Refuses uncommitted or locally edited files."""
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if require_committed:
        repo = repo_root(path)
        rel = str(path.resolve().relative_to(repo.resolve()))
        if not _git(repo, "ls-files", rel).strip():
            raise PreregError(f"{rel} is not committed: pre-register it before running")
        if _git(repo, "status", "--porcelain", "--", rel).strip():
            raise PreregError(f"{rel} has local changes: commit them (a new version) first")
    return Prereg.model_validate(yaml.safe_load(raw)), sha
