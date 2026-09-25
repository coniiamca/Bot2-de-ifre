"""H5 feature: quarter-hour opening imbalance, hourly means and trailing z-scores use only
the past."""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")

from quanta.research.burst import hourly_mean, opening_imbalance, trailing_z  # noqa: E402
from quanta.research.minute import MIN_NS  # noqa: E402
from quanta.research.panel import HOUR_NS  # noqa: E402
from quanta.research.synthetic import make_minute_bars  # noqa: E402


def test_opening_minutes_and_their_imbalance() -> None:
    b = make_minute_bars(2, seed=1)
    b.tb[:] = b.v * 0.75  # every minute: 75 % aggressive buying → OI = 0.5
    avail, oi = opening_imbalance(b)
    assert avail.size == 2 * 24 * 4
    minutes = ((avail - MIN_NS) // MIN_NS) % 60
    assert set(minutes.tolist()) == {0, 15, 30, 45}
    assert np.allclose(oi, 0.5)


def test_hourly_mean_uses_only_known_values() -> None:
    avail = np.array([10, 20, 30, 70], dtype=np.int64) * MIN_NS
    value = np.array([1.0, 2.0, 3.0, 10.0])
    grid = np.array([0, 60, 120], dtype=np.int64) * MIN_NS
    mean, last = hourly_mean(avail, value, grid, 1)
    assert np.isnan(mean[0]) and last[0] == -1
    assert mean[1] == pytest.approx(2.0) and last[1] == 30 * MIN_NS  # 70 not yet known
    assert mean[2] == pytest.approx(10.0)  # only (60, 120] minutes
    four, _ = hourly_mean(avail, value, grid, 4)
    assert four[2] == pytest.approx(4.0)
    assert HOUR_NS == 60 * MIN_NS


def test_trailing_z_excludes_the_current_value_and_the_future() -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(size=1000)
    z = trailing_z(x, window=200, min_obs=50)
    assert np.isnan(z[:50]).all() and np.isfinite(z[50:]).all()
    t = 500
    past = x[t - 200 : t]
    assert z[t] == pytest.approx((x[t] - past.mean()) / past.std())
    y = x.copy()
    y[t + 1 :] += 100.0  # changing the future never changes an earlier z
    assert np.array_equal(trailing_z(y, 200, 50)[: t + 1], z[: t + 1], equal_nan=True)


def test_reads_stay_within_the_fetch_window(tmp_path: object) -> None:
    """Data fetched later for another universe must not change a result: the loaders only
    read the months the universe's own fetch downloaded."""
    from pathlib import Path

    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanta.research.data import _read
    from quanta.research.minute import load_bars

    root = Path(str(tmp_path))
    for name, month in (
        ("klines_1h", "2020-01"),
        ("klines_1h", "2020-02"),
        ("klines_1m", "2020-02"),
    ):
        d = root / "lake" / "binance_vision_um" / name / "symbol=AUSDT" / f"month={month}"
        d.mkdir(parents=True)
        t0 = 1_577_836_800_000 if month == "2020-01" else 1_580_515_200_000
        table = pa.table(
            {
                "open_time": pa.array([t0 * 10**6], pa.timestamp("ns")),
                **{c: [1.0] for c in ("open", "high", "low", "close", "volume")},
                "quote_volume": [1.0],
                "taker_buy_volume": [0.5],
            }
        )
        pq.write_table(table, d / "part-0.parquet")
    assert _read(root, "klines_1h", "AUSDT", ["open"]).num_rows == 2  # type: ignore[union-attr]
    assert _read(root, "klines_1h", "AUSDT", ["open"], ("2020-01", "2020-01")).num_rows == 1  # type: ignore[union-attr]
    uni = [("2020-02", 1, "AUSDT")]
    assert load_bars(root, uni, "AUSDT", "2020-01-01", "2020-03-01") is not None
    assert load_bars(root, uni, "AUSDT", "2020-01-01", "2020-03-01", ("2020-01", "2020-01")) is None
