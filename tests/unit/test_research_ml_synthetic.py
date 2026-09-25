"""Model M1 pipeline on synthetic markets: a planted flow effect is found out of sample and
earns before costs through the real engine; a market without it is not; a feature that sees
the future is caught by the leak guard."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("lightgbm")

from quanta.research.features import Frame, assemble, symbol_frame  # noqa: E402
from quanta.research.gates import annual_sr  # noqa: E402
from quanta.research.intraday import (  # noqa: E402
    IntradayCosts,
    Portfolio,
    Trades,
    combine,
    daily_pnl,
    run_trades,
)
from quanta.research.minute import MIN_NS, month_of  # noqa: E402
from quanta.research.ml import ModelSpec, entries, walk_forward  # noqa: E402
from quanta.research.ml_runner import LEAK_SR, Settings, _signals  # noqa: E402
from quanta.research.synthetic import make_minute_bars  # noqa: E402

H = 60
MONTHS = ["2020-03", "2020-04"]
FREE = IntradayCosts(taker_bps=0.0, maker_bps=0.0, slip_bps=(0.0, 0.0, 0.0, 0.0))


def market(beta: float) -> tuple[list[Any], Frame]:
    bars = [
        make_minute_bars(n_days=121, seed=40 + s, shock_rate=0.0, flow_beta=beta, rank=s + 1)
        for s in range(5)
    ]
    frames = [symbol_frame(b, s, (H,)) for s, b in enumerate(bars)]
    return bars, assemble(frames, frames[0])


def spec(kind: str) -> ModelSpec:
    s = ModelSpec(kind, train_days=40, calib_days=10, rounds=40, label_span_min=H + 12)
    return replace(s, params=dict(s.params, min_data_in_leaf=500, num_threads=2))


def trade(bars: list[Any], f: Frame, kind: str) -> tuple[float, float, Any]:
    rows = np.ones(len(f), dtype=bool)
    fc = walk_forward(f, H, spec(kind), MONTHS, rows, (0.02,))
    test = np.isfinite(fc.pred)
    both = test & np.isfinite(f.y[H])
    corr = float(np.corrcoef(fc.pred[both], f.y[H][both])[0, 1])
    months = month_of(f.t - MIN_NS)
    idx, side = entries(f, fc, 0.02, rows, months)
    st = Settings.of({})
    parts = []
    for s, b in enumerate(bars):
        sel = f.sym[idx] == s
        order = (f.t[idx][sel], side[sel], f.sigma[idx][sel], H)
        parts.append(run_trades(b, _signals(b, order, st, "maker"), FREE).with_symbol(s))
    tr, w = combine(Trades.concat(parts), Portfolio())
    first = int(f.t[test].min() // (86400 * 10**9))
    daily = daily_pnl(tr, w, first, first + 60)
    return corr, annual_sr(daily), daily


@pytest.mark.parametrize("kind", ["ridge", "lgbm"])
def test_planted_flow_is_found_and_earns_before_costs(kind: str) -> None:
    bars, f = market(0.15)
    corr, sr, daily = trade(bars, f, kind)
    assert corr > 0.05
    assert daily.sum() > 0 and sr > 3.0


def test_no_effect_no_forecast_power() -> None:
    bars, f = market(0.0)
    corr, _, _ = trade(bars, f, "ridge")
    assert abs(corr) < 0.03


def test_a_feature_that_sees_the_future_trips_the_leak_guard() -> None:
    bars, f = market(0.0)
    # replace one feature by the label itself (known only after the decision)
    f.x[:, 0] = np.nan_to_num(f.y[H], nan=0.0)
    _, sr, _ = trade(bars, f, "ridge")
    assert sr > LEAK_SR
