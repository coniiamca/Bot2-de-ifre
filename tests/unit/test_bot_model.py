"""The bot's model bundle and training recipe: the recipe is M1's pre-registered best setting,
a bundle survives a save/load with the same predictions, the newest started month is used."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

np = pytest.importorskip("numpy")
lgb = pytest.importorskip("lightgbm")

from quanta.bot.model import ModelBundle  # noqa: E402
from quanta.bot.train import HORIZON, M1_FIXED, REPO, Q, shift  # noqa: E402


def test_the_recipe_is_m1s_preregistered_best_setting() -> None:
    m1 = yaml.safe_load((REPO / "research" / "prereg" / "M1.yaml").read_text())
    for k, v in M1_FIXED.items():
        assert m1["fixed"][k] == v, k
    r1 = yaml.safe_load((REPO / "research" / "prereg" / "R1.yaml").read_text())
    best = r1["fixed"]["configs"]["R1-60"]
    assert (best["horizon"], best["q"]) == (HORIZON, Q)
    assert HORIZON in m1["grid"]["horizon"] and Q in m1["grid"]["q"]


def bundle(month: str = "2026-10") -> ModelBundle:
    rng = np.random.default_rng(0)
    x = rng.normal(size=(500, 3)).astype(np.float32)
    y = x[:, 0] + rng.normal(size=500) * 0.1
    params = {"objective": "regression", "verbose": -1, "num_threads": 1, "min_data_in_leaf": 20}
    booster = lgb.train(params, lgb.Dataset(x, label=y), num_boost_round=10)
    return ModelBundle(
        month=month,
        horizon=60,
        q=0.0025,
        low=-1.0,
        high=1.0,
        features=["a", "b", "c"],
        universe=[(1, "BTCUSDT"), (2, "ETHUSDT")],
        usdc=["BTCUSDT"],
        model_text=booster.model_to_string(),
    )


def test_bundle_round_trip_and_sides(tmp_path: Path) -> None:
    b = bundle()
    path = b.save(tmp_path)
    again = ModelBundle.load(path)
    x = np.array([[2.0, 0, 0], [-2.0, 0, 0], [0.0, 0, 0]], dtype=np.float32)
    assert np.allclose(b.predict(x), again.predict(x))
    assert again.universe == [(1, "BTCUSDT"), (2, "ETHUSDT")]
    assert [again.side(v) for v in (1.5, -1.5, 0.3, float("nan"))] == [1, -1, 0, 0]


def test_latest_skips_future_months_and_age(tmp_path: Path) -> None:
    for m in ("2026-08", "2026-09", "2026-11"):
        bundle(m).save(tmp_path)
    now = int(datetime(2026, 10, 2, tzinfo=UTC).timestamp() * 1e9)
    b = ModelBundle.latest(tmp_path, now)
    assert b is not None and b.month == "2026-09"
    assert b.age_days(now) == pytest.approx(1.0)  # September ended a day ago
    assert ModelBundle.latest(tmp_path / "none", now) is None
    assert shift("2026-01", -2) == "2025-11" and shift("2026-12", 1) == "2027-01"
