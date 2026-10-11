"""``quanta bot egit``: train the model for a month exactly as the research tested it.

The recipe is M1's best setting (``research/prereg/M1.yaml``, re-checked by R1): LightGBM on
the M1 features of the month's top-10 USDT perpetuals, trained on the 12 months before a
30-day calibration slice, recent rows weighted more (half-life 90 days), entry thresholds at
the calibration predictions' extreme 0.25 %, 60-minute horizon.

Data comes from Binance's public archive (data.binance.vision, no key, no rate limit):
1-minute klines, funding, 5-minute premium index and metrics for the universe, 1-minute
klines of the USDC contracts (for their eligibility). The first run downloads ~2 GB; later
months only add what is new.
"""

from __future__ import annotations

import csv
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from quanta.bot.model import ModelBundle
from quanta.core.log import get_logger
from quanta.research.fetch import M1_EXTRA, MINUTE, USDC, fetch_dataset, usdc_counterparts
from quanta.research.ml import fit_month
from quanta.research.ml_runner import Settings, build_frame, usdc_eligibility
from quanta.research.universe import (
    UNIVERSE_HEADER,
    build_universe,
    month_range,
    read_exclusions,
    read_universe,
)

log = get_logger(__name__)
REPO = Path(__file__).resolve().parents[3]

# M1's best setting — the values of research/prereg/M1.yaml (a test keeps them equal)
HORIZON = 60
Q = 0.0025
M1_FIXED: dict[str, Any] = {
    "top": 10,
    "entry_valid": 5,
    "exit_valid": 5,
    "stop_k": 3.0,
    "usdc_first_month": "2024-01",
    "usdc_from": "2024-02",
    "usdc_min_qv_day": 5_000_000,
    "usdc_gap_fill": 60,
    "model": {
        "train_days": 365,
        "calib_days": 30,
        "gap_days": 1,
        "halflife_days": 90,
        "rounds": 300,
        "ridge_lambda": 0.001,
        "min_train_rows": 20000,
        "lgbm": {
            "learning_rate": 0.05,
            "num_leaves": 31,
            "min_data_in_leaf": 2000,
            "feature_fraction": 0.8,
            "bagging_fraction": 0.8,
            "bagging_freq": 1,
            "lambda_l2": 10.0,
            "max_bin": 63,
            "num_threads": 4,
            "seed": 0,
        },
    },
}
UNIVERSE_TOP = 20  # ranks kept in the universe file (the model uses the top 10)


def shift(month: str, n: int) -> str:
    y, m = divmod(int(month[:4]) * 12 + int(month[5:7]) - 1 + n, 12)
    return f"{y:04d}-{m + 1:02d}"


def current_month(now_ns: int | None = None) -> str:
    now = datetime.fromtimestamp(now_ns / 1e9, UTC) if now_ns else datetime.now(UTC)
    return now.strftime("%Y-%m")


def _write_universe(path: Path, rows: list[tuple[str, int, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(UNIVERSE_HEADER[:3])
        for m, r, s in sorted(rows):
            w.writerow([m, r, s])


async def universe_rows(root: Path, month: str) -> list[tuple[str, int, str]]:
    """Universe rows for ``month`` − 14 … ``month``: the repo's point-in-time file plus the
    months computed here (same method), cached in ``<root>/universe.csv``."""
    need = month_range(shift(month, -14), month)
    rows: dict[tuple[str, int], str] = {}
    for f in (REPO / "research" / "universe" / "um_top20.csv", root / "universe.csv"):
        if f.exists():
            for m, r, s in read_universe(f):
                if m in need:
                    rows[(m, r)] = s
    missing = [m for m in need if not any(k[0] == m for k in rows)]
    if missing:
        log.warning("bot_universe_build", months=f"{missing[0]}..{month}")
        built = await build_universe(
            root,
            missing[0],
            month,
            root / "universe-new.csv",
            root / "manifest_1d.csv.gz",
            top_n=UNIVERSE_TOP,
            exclude=read_exclusions(REPO / "research" / "universe" / "non_crypto.txt"),
        )
        for row in built:
            rows[(str(row[0]), int(row[1]))] = str(row[2])
        lacking = [m for m in need if not any(k[0] == m for k in rows)]
        if lacking:
            raise RuntimeError(
                f"arşivde {lacking} ayları için veri yok (önceki ayın aylık dosyaları her ayın "
                "ilk günlerinde yayınlanır; birkaç saat sonra tekrar deneyin)"
            )
        cached = read_universe(root / "universe.csv") if (root / "universe.csv").exists() else []
        merged = {(m, r): s for m, r, s in cached} | rows
        _write_universe(root / "universe.csv", [(m, r, s) for (m, r), s in merged.items()])
    return sorted((m, r, s) for (m, r), s in rows.items())


async def fetch_training_data(
    root: Path, month: str, universe: list[tuple[str, int, str]], top: int = 10
) -> dict[str, int]:
    """Download (or reuse) everything the month's model needs."""
    train = [u for u in universe if shift(month, -13) <= u[0] <= shift(month, -1) and u[1] <= top]
    mdir = root / "manifests"
    got = await fetch_dataset(
        root,
        train,
        mdir / f"klines_1m-{month}.csv.gz",
        datasets=(*MINUTE, ("fundingRate", "")),
        pad_before=1,
        pad_after=0,
        metrics_symbols=(),
    )
    symbols = sorted({s for _, _, s in train})
    got += await fetch_dataset(
        root,
        train,
        mdir / f"m1-{month}.csv.gz",
        datasets=M1_EXTRA,
        pad_before=1,
        pad_after=0,
        metrics_symbols=tuple(symbols),
        metrics_pad=(1, 0),
    )
    current = [u for u in universe if u[0] == month and u[1] <= top]
    pairs = await usdc_counterparts(sorted({s for _, _, s in current}))
    usdc_rows = [(m, 0, c) for c in pairs.values() for m in (shift(month, -2), shift(month, -1))]
    got += await fetch_dataset(
        root,
        usdc_rows,
        mdir / f"usdc-{month}.csv.gz",
        datasets=USDC,
        pad_before=0,
        pad_after=0,
        metrics_symbols=(),
    )
    failed = [u.listed.key for u in got if u.result.path is None]
    if failed:
        raise RuntimeError(f"{len(failed)} arşiv dosyası indirilemedi, örn. {failed[:3]}")
    return {"files": len(got), "usdc_pairs": len(pairs)}


def train_bundle(
    root: Path,
    month: str,
    universe: list[tuple[str, int, str]],
    *,
    workers: int | None = None,
) -> ModelBundle:
    st = Settings.of(M1_FIXED)
    if workers is None and sys.platform == "win32":
        workers = 1  # spawned workers and console-script launchers do not mix on Windows
    start = shift(month, -14) + "-01"
    frame, symbols = build_frame(root, universe, st.top, start, month + "-01", (HORIZON,), workers)
    train_rows = (frame.rank >= 1) & (frame.rank <= st.top)
    fm = fit_month(frame, HORIZON, st.model("lgbm", HORIZON), month, train_rows, (Q,))
    low, high = fm.thresholds[Q]
    current = sorted((r, s) for m, r, s in universe if m == month and r <= st.top)
    eligible = usdc_eligibility(root, [s for _, s in current], st, month, workers)
    usdc = [s for _, s in current if eligible.get((month, s), False)]
    top = dict(sorted(fm.importance.items(), key=lambda kv: -kv[1])[:10])
    return ModelBundle(
        month=month,
        horizon=HORIZON,
        q=Q,
        low=low,
        high=high,
        features=list(frame.names),
        universe=current,
        usdc=usdc,
        model_text=fm.model_text,
        importance=top,
        info={
            "train_rows": fm.train_rows,
            "calib_rows": fm.calib_rows,
            "slices": [
                datetime.fromtimestamp(x / 1e9, UTC).date().isoformat() for x in fm.slices_ns
            ],
            "symbols_seen": symbols,
            "frame_rows": len(frame),
            "created": datetime.now(UTC).isoformat(timespec="seconds"),
            "recipe": "M1 best (R1): lgbm, h=60, q=0.0025",
            "pred_calib_quantiles": {"low": low, "high": high},
            "frame_nan_share": float(np.mean(~np.isfinite(frame.x))),
        },
    )
