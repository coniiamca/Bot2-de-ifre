"""The trained model the bot trades with: one file per month (``model-YYYY-MM.json``),
written by ``quanta bot egit`` and read at start-up.

A bundle holds the LightGBM model, the month's entry thresholds (from the calibration slice
the model never saw), the feature order, the month's universe (the top USDT perpetuals by
the previous month's volume, ranked) and the coins whose USDC contract may be traded.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

F32 = NDArray[np.float32]


@dataclass(slots=True)
class ModelBundle:
    month: str  # the month this model trades (YYYY-MM)
    horizon: int  # minutes held (the label's horizon)
    q: float  # selectivity: the extreme q of calibration predictions trade
    low: float  # prediction below → short
    high: float  # prediction above → long
    features: list[str]
    universe: list[tuple[int, str]]  # (rank, USDT symbol) of the month
    usdc: list[str]  # USDT symbols whose USDC contract is eligible this month
    model_text: str = field(repr=False)
    importance: dict[str, float] = field(default_factory=dict)
    info: dict[str, Any] = field(default_factory=dict)  # rows, slices, data, created
    _booster: Any = field(default=None, repr=False, compare=False)

    # -- prediction ------------------------------------------------------------------------
    def predict(self, x: F32) -> F32:
        if self._booster is None:
            import lightgbm as lgb

            self._booster = lgb.Booster(model_str=self.model_text)
        out: F32 = np.asarray(self._booster.predict(x), dtype=np.float32)
        return out

    def side(self, pred: float) -> int:
        """+1 long, −1 short, 0 no trade."""
        if not np.isfinite(pred):
            return 0
        if pred > self.high:
            return 1
        if pred < self.low:
            return -1
        return 0

    def age_days(self, now_ns: int) -> float:
        """Days since the end of the model's month (≤ 0 while the month runs)."""
        y, m = int(self.month[:4]), int(self.month[5:7])
        end = datetime(y + m // 12, m % 12 + 1, 1, tzinfo=UTC).timestamp()
        return (now_ns / 1e9 - end) / 86400.0

    # -- files -----------------------------------------------------------------------------
    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        doc = {k: v for k, v in asdict(self).items() if k != "_booster"}
        path = directory / f"model-{self.month}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        tmp.replace(path)
        return path

    @staticmethod
    def load(path: Path) -> ModelBundle:
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["universe"] = [(int(r), str(s)) for r, s in doc["universe"]]
        return ModelBundle(**doc)

    @staticmethod
    def latest(directory: Path, now_ns: int | None = None) -> ModelBundle | None:
        """The newest model whose month has begun (a model for a future month is skipped)."""
        now = datetime.fromtimestamp((now_ns or 0) / 1e9, UTC) if now_ns else datetime.now(UTC)
        current = now.strftime("%Y-%m")
        files = sorted(
            p
            for p in directory.glob("model-????-??.json")
            if p.stem.removeprefix("model-") <= current
        )
        return ModelBundle.load(files[-1]) if files else None
