"""Walk-forward forecasts of model M1 (``research/prereg/M1.yaml``).

For every test month the model is fitted again on the past only:

    ... train (≤ 365 d) | gap | calibration (30 d) | gap | test month ...

* training rows: universe rows whose label ends before the calibration slice starts;
* calibration rows: predicted by that model, out of sample; their prediction quantiles
  become the month's entry thresholds (so the thresholds never use the test month nor the
  model's own training rows);
* every boundary has a gap of at least one day, longer than the longest label plus the
  latency and the exit window, so no label crosses into the next slice (asserted);
* recent rows weigh more (half-life ``halflife_days``);
* feature scaling (ridge) is fitted on the training rows only.

Hyperparameters are fixed in the pre-registration; nothing is tuned on results. LightGBM is
imported lazily (the recording server does not install it).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
from numpy.typing import NDArray

from quanta.core.log import get_logger
from quanta.research.features import Frame
from quanta.research.minute import MIN_NS

log = get_logger(__name__)
Floats = NDArray[np.float64]
F32 = NDArray[np.float32]
DAY_NS = 86400 * 10**9
LGBM_PARAMS: dict[str, Any] = {
    "objective": "regression",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 2000,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 10.0,
    "max_bin": 63,
    "deterministic": True,
    "force_row_wise": True,
    "num_threads": 4,
    "seed": 0,
    "verbose": -1,
}
MIN_TRAIN_ROWS = 20_000


@dataclass(frozen=True, slots=True)
class ModelSpec:
    kind: str  # "lgbm" | "ridge"
    train_days: int = 365
    calib_days: int = 30
    gap_days: int = 1
    halflife_days: float = 90.0
    rounds: int = 300
    ridge_lambda: float = 1e-3  # × the total weight; features are standardised
    params: dict[str, Any] = field(default_factory=lambda: dict(LGBM_PARAMS))
    label_span_min: int = 0  # longest label + latency + exit window (for the gap assert)
    min_train_rows: int = MIN_TRAIN_ROWS


@dataclass(slots=True)
class Forecast:
    pred: F32  # one per frame row; NaN outside the test months
    thresholds: dict[str, dict[float, tuple[float, float]]]  # month → q → (low, high)
    importance: dict[str, float]  # mean share of gain (lgbm) or |coef| (ridge)
    fits: list[dict[str, Any]]  # per month: rows used


def month_start(month: str) -> int:
    d = datetime.fromisoformat(month + "-01").replace(tzinfo=UTC)
    return int(d.timestamp()) * 10**9


def next_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y + m // 12:04d}-{m % 12 + 1:02d}"


def slices(month: str, spec: ModelSpec) -> tuple[int, int, int, int, int, int]:
    """[r0, r1) training, [c0, c1) calibration and [s0, s1) test times (ns) of a month."""
    gap = spec.gap_days * DAY_NS
    s0, s1 = month_start(month), month_start(next_month(month))
    c1 = s0 - gap
    c0 = c1 - spec.calib_days * DAY_NS
    r1 = c0 - gap
    return r1 - spec.train_days * DAY_NS, r1, c0, c1, s0, s1


def _fit_predict(
    spec: ModelSpec, x: F32, y: F32, w: Floats, targets: list[F32]
) -> tuple[list[F32], Floats]:
    if spec.kind == "lgbm":
        import lightgbm as lgb

        ds = lgb.Dataset(x, label=y, weight=w, free_raw_data=True)
        booster = lgb.train(spec.params, ds, num_boost_round=spec.rounds)
        gain = np.asarray(booster.feature_importance("gain"), dtype=np.float64)
        preds = [np.asarray(booster.predict(t), dtype=np.float32) for t in targets]
        return preds, gain / max(float(gain.sum()), 1e-12)
    if spec.kind == "ridge":
        xd = x.astype(np.float64)
        fin = np.isfinite(xd)
        wsum = float(w.sum())
        mu = np.nansum(np.where(fin, xd, 0.0) * w[:, None], axis=0) / np.maximum(
            (fin * w[:, None]).sum(axis=0), 1e-12
        )
        dev = np.where(fin, xd - mu, 0.0)
        sd = np.sqrt((dev * dev * w[:, None]).sum(axis=0) / max(wsum, 1e-12))
        sd = np.where(sd > 0, sd, 1.0)

        def design(a: F32) -> Floats:
            z = (a.astype(np.float64) - mu) / sd
            z = np.where(np.isfinite(z), z, 0.0)  # missing → the training mean
            return np.column_stack([np.ones(len(a)), z])

        d = design(x)
        a = d.T @ (d * w[:, None])
        pen = spec.ridge_lambda * wsum * np.eye(a.shape[0])
        pen[0, 0] = 0.0  # the intercept is not shrunk
        beta = np.linalg.solve(a + pen, d.T @ (w * y.astype(np.float64)))
        preds = [(design(t) @ beta).astype(np.float32) for t in targets]
        coef = np.abs(beta[1:])
        return preds, coef / max(float(coef.sum()), 1e-12)
    raise ValueError(f"unknown model kind {spec.kind!r}")


def walk_forward(
    frame: Frame,
    horizon: int,
    spec: ModelSpec,
    months: list[str],
    train_rows: NDArray[np.bool_],
    qs: tuple[float, ...],
    *,
    shuffle_seed: int | None = None,
) -> Forecast:
    """Monthly refits for ``months`` (YYYY-MM). ``train_rows``: rows allowed in training and
    calibration (the universe). ``shuffle_seed``: permute the training labels (timing runs
    and null tests: no information, same cost)."""
    if spec.label_span_min * MIN_NS >= spec.gap_days * DAY_NS:
        raise ValueError("the gap between slices must exceed the label span")
    y_all = frame.y[horizon]
    usable = train_rows & np.isfinite(y_all)
    pred = np.full(len(frame), np.nan, dtype=np.float32)
    thresholds: dict[str, dict[float, tuple[float, float]]] = {}
    imp = np.zeros(frame.x.shape[1])
    fits: list[dict[str, Any]] = []
    rng = np.random.default_rng(shuffle_seed) if shuffle_seed is not None else None
    t = frame.t
    for month in months:
        r0, r1, c0, c1, s0, s1 = slices(month, spec)
        tr = np.flatnonzero(usable & (t >= r0) & (t < r1))
        ca = np.flatnonzero(usable & (t >= c0) & (t < c1))
        te = np.flatnonzero((t >= s0) & (t < s1))
        fits.append({"month": month, "train": int(tr.size), "calib": int(ca.size)})
        if tr.size < spec.min_train_rows or ca.size == 0 or te.size == 0:
            log.info("ml_month_skipped", month=month, train=int(tr.size), calib=int(ca.size))
            continue
        y = y_all[tr]
        if rng is not None:
            y = y[rng.permutation(y.size)]
        w = 0.5 ** ((r1 - t[tr]) / (spec.halflife_days * DAY_NS))
        (p_cal, p_test), importance = _fit_predict(
            spec, frame.x[tr], y, w, [frame.x[ca], frame.x[te]]
        )
        pred[te] = p_test
        imp += importance
        thresholds[month] = {
            q: (float(np.quantile(p_cal, q)), float(np.quantile(p_cal, 1.0 - q))) for q in qs
        }
        log.info("ml_month_done", month=month, h=horizon, kind=spec.kind, train=int(tr.size))
    fitted = max(len(thresholds), 1)
    names = frame.names
    return Forecast(
        pred,
        thresholds,
        {names[k]: float(imp[k] / fitted) for k in range(len(names))},
        fits,
    )


def entries(
    frame: Frame,
    fc: Forecast,
    q: float,
    tradable: NDArray[np.bool_],
    months: NDArray[np.str_],
) -> tuple[NDArray[np.int64], NDArray[np.int8]]:
    """Rows to trade and their sides: prediction above the month's upper threshold → long,
    below the lower → short."""
    side = np.zeros(len(frame), dtype=np.int8)
    for month, per_q in fc.thresholds.items():
        lo, hi = per_q[q]
        sel = tradable & (months == month) & np.isfinite(fc.pred)
        side[sel & (fc.pred > hi)] = 1
        side[sel & (fc.pred < lo)] = -1
    rows = np.flatnonzero(side != 0).astype(np.int64)
    return rows, side[rows]
