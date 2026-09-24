"""Configuration loading.

Configuration is YAML validated by pydantic models (fail fast on unknown or invalid keys).
Secrets are never stored in configuration files: a config may only *reference* a secret file
path (see ADR-007).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from quanta.core.errors import ConfigError


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def load_yaml_config[T: BaseModel](path: str | Path, model: type[T]) -> T:
    p = Path(path)
    try:
        raw: Any = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read config {p}: {exc}") from exc
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid config {p}:\n{exc}") from exc


def config_hash(cfg: BaseModel) -> str:
    """Stable hash of a config, recorded with every run for reproducibility."""
    canonical = json.dumps(cfg.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
