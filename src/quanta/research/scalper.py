"""The quiet-market scalper (``research/prereg/G1.yaml``): small, frequent maker trades on the
USDC-margined contracts, the user's "10× ile sürekli ufak ufak kâr" asked as precisely as
possible.

Every 5 minutes, on each eligible USDC contract that is flat:

* **Quiet filter** (optional): the last hour went nowhere (efficiency ratio
  ``|c_t − c_{t−60}| / Σ|Δc| < er_max``) and its volatility is not above normal
  (``rv_60 / σ < rv_max``).
* **Two post-only limits:** a buy ``d`` below and a sell ``d`` above the close,
  ``d = a · σ · √15`` (σ: EWMA of 1-minute returns, half-life one day). They rest for
  ``valid`` minutes; the first one traded *through* opens the position, the other is
  cancelled. When one bar trades through both, the side the bar then moved against is taken
  (pessimistic).
* **Exits** by the minute engine (:mod:`quanta.research.intraday`): a maker take-profit
  ``tp · d`` away (filled only when traded through, not in the entry bar), a stop-market
  ``stop · d`` away (taker fee, double slippage; wins ties with the target), a maker time
  exit after ``hold`` minutes then a market order, funding at funding times.

Eligible: USDC contracts listed for at least one full month whose previous month traded at
least ``min_qv_day`` a day; ranked by that volume (the rank sets the slippage tier).

The run: every setting of the grid on the development period, the best by annual Sharpe
(≥ ``min_trades`` trades) chosen by a rule fixed beforehand, then that one setting once on
the test period. Every setting counts as a trial in the ledger.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from quanta.core.log import get_logger
from quanta.research.gates import annual_sr, max_drawdown
from quanta.research.intraday import (
    Portfolio,
    Signals,
    Trades,
    combine,
    daily_pnl,
    ewma,
    run_trades,
)
from quanta.research.intraday_runner import _stats, costs_of
from quanta.research.ledger import Ledger, trial_id
from quanta.research.minute import Bars, fill_gaps, load_bars
from quanta.research.ml_runner import _pool, _usdc_volume_job, data_sha
from quanta.research.prereg import Prereg, git_state
from quanta.research.recent_test import Sizing, dollar_stats, outcome
from quanta.research.report import OUTCOME_TEXT, num, pct, usd
from quanta.research.runner import _day, _iso
from quanta.research.universe import month_range

log = get_logger(__name__)
Floats = NDArray[np.float64]
Ints = NDArray[np.int64]
DAY_MIN = 1440
WARMUP_MIN = 3 * DAY_MIN


@dataclass(frozen=True, slots=True)
class ScalperParams:
    a: float  # entry distance d = a · σ · √15
    tp: float  # take-profit, in units of d from the entry
    stop: float  # stop, in units of d from the entry
    hold: int  # minutes
    quiet: bool  # the quiet-market filter
    er_max: float = 0.3
    rv_max: float = 1.5
    valid: int = 5
    exit_valid: int = 5

    @staticmethod
    def of(cfg: dict[str, Any], fixed: dict[str, Any]) -> ScalperParams:
        merged = {**fixed.get("params", {}), **cfg}
        names = ScalperParams.__dataclass_fields__
        return ScalperParams(**{k: v for k, v in merged.items() if k in names})

    def signals(self, bars: Bars) -> Signals:
        return scalper_signals(bars, self)

    def label(self) -> str:
        quiet = "durgun filtre" if self.quiet else "filtre yok"
        return f"a {num(self.a, 1)}, kâr al {num(self.tp, 1)}·d, {quiet}"


class Setting(Protocol):
    """A strategy setting the selection run can simulate and describe."""

    def signals(self, bars: Bars) -> Signals: ...

    def label(self) -> str: ...


def _shift(month: str, n: int) -> str:
    y, m = divmod(int(month[:4]) * 12 + int(month[5:7]) - 1 + n, 12)
    return f"{y:04d}-{m + 1:02d}"


def eligible_rows(
    root: Path, symbols: list[str], months: list[str], min_qv_day: float, top: int, workers: int
) -> list[tuple[str, int, str]]:
    """Point-in-time (month, rank, symbol) rows of the eligible USDC contracts."""
    lo, hi = _shift(months[0], -2), months[-1]
    vols = dict(_pool(_usdc_volume_job, [(root, s, lo, hi) for s in symbols], workers))
    rows: list[tuple[str, int, str]] = []
    for m in months:
        prev = _shift(m, -1)
        ok = []
        for s, per in vols.items():
            if not per or min(per) > _shift(m, -2):
                continue  # not listed for a full month before m
            qv = per.get(prev, 0.0)
            if qv >= min_qv_day:
                ok.append((qv, s))
        ok.sort(reverse=True)
        rows += [(m, r + 1, s) for r, (_, s) in enumerate(ok[:top])]
    return rows


def _quiet(bars: Bars, i: Ints, sigma: Floats, r1: Floats, p: ScalperParams) -> NDArray[np.bool_]:
    m = bars.minute
    j = np.searchsorted(m, m[i] - 60, side="right") - 1
    ok = (j >= 0) & (m[i] - m[np.maximum(j, 0)] <= 65)
    j = np.maximum(j, 0)
    logc = np.log(bars.c)
    a = np.abs(np.nan_to_num(r1))
    ca = np.concatenate([[0.0], np.cumsum(a)])
    c2 = np.concatenate([[0.0], np.cumsum(np.nan_to_num(r1) ** 2)])
    path = ca[i + 1] - ca[j + 1]
    with np.errstate(divide="ignore", invalid="ignore"):
        er = np.abs(logc[i] - logc[j]) / path
        rv = np.sqrt((c2[i + 1] - c2[j + 1]) / 60.0) / sigma[i]
    out: NDArray[np.bool_] = ok & (path > 0) & (er < p.er_max) & (rv < p.rv_max)
    return out


def scalper_signals(bars: Bars, p: ScalperParams) -> Signals:
    """One signal per 5-minute decision whose two-sided entry gets a fill (the side that
    fills first); the engine then fills it at that bar and runs the exits."""
    n = len(bars)
    m = bars.minute
    empty = Signals.build(
        np.zeros(0, np.int64), np.zeros(0), np.zeros(0), np.zeros(0), np.zeros(0), p.hold
    )
    if n < WARMUP_MIN + 10:
        return empty
    logc = np.log(bars.c)
    r1 = np.full(n, np.nan)
    r1[1:] = np.diff(logc)
    r1[1:][np.diff(m) != 1] = np.nan
    sigma = np.sqrt(ewma(r1 * r1, float(DAY_MIN)))
    i = np.flatnonzero((m % 5 == 4) & (m >= m[0] + WARMUP_MIN) & (bars.rank > 0))
    i = i[np.isfinite(sigma[i]) & (sigma[i] > 0) & (i + p.valid < n)]
    if p.quiet:
        i = i[_quiet(bars, i, sigma, r1, p)]
    if i.size == 0:
        return empty
    d = p.a * sigma[i] * math.sqrt(15.0)
    buy, sell = bars.c[i] * (1.0 - d), bars.c[i] * (1.0 + d)
    side = np.zeros(i.size, dtype=np.int8)
    done = np.zeros(i.size, dtype=bool)
    for k in range(1, p.valid + 1):
        j = i + k
        live = ~done & (m[j] == m[i] + k)  # the window must be contiguous
        done |= ~(m[j] == m[i] + k)
        traded = bars.v[j] > 0
        b = live & traded & (bars.low[j] < buy)
        s = live & traded & (bars.h[j] > sell)
        both = b & s
        ended_up = bars.c[j] >= (bars.h[j] + bars.low[j]) / 2.0
        side[b & ~s] = 1
        side[s & ~b] = -1
        side[both] = np.where(ended_up[both], -1, 1)  # the side the bar went against
        done |= b | s
    keep = side != 0
    i, side, d = i[keep], side[keep], d[keep]
    level = np.where(side > 0, buy[keep], sell[keep])
    return Signals.build(
        i,
        side,
        level,
        level * (1.0 - side * p.stop * d),
        level * (1.0 + side * p.tp * d),
        p.hold,
        p.valid,
        post_only=True,
        exit_valid=p.exit_valid,
    )


def _sim_job(args: tuple[Any, ...]) -> tuple[str, dict[str, Trades]]:
    root, rows, symbol, start, end, settings, costs = args
    months = sorted({m for m, _, s in rows if s == symbol})
    if not months:
        return symbol, {}
    bars = load_bars(root, rows, symbol, start, end, ("2024-01", months[-1]))
    if bars is None:
        return symbol, {}
    bars = fill_gaps(bars, 60)
    out = {}
    for name, p in settings.items():
        out[name] = run_trades(bars, p.signals(bars), costs)
    return symbol, out


def simulate(
    root: Path,
    rows: list[tuple[str, int, str]],
    start: str,
    end: str,
    settings: dict[str, Setting],
    costs: Any,
    workers: int,
) -> tuple[dict[str, Trades], list[str]]:
    symbols = sorted({s for _, _, s in rows})
    jobs = [(root, rows, s, start, end, settings, costs) for s in symbols]
    parts: dict[str, list[Trades]] = {k: [] for k in settings}
    for k, (_, res) in enumerate(_pool(_sim_job, jobs, workers)):
        for name, tr in res.items():
            parts[name].append(tr.with_symbol(k))
    return {name: Trades.concat(v) for name, v in parts.items()}, symbols


def _evaluate(
    tr: Trades,
    pf: Portfolio,
    first_day: int,
    end_day: int,
    capital: float,
    limits: list[float],
    symbols: list[str],
) -> tuple[dict[str, Any], Floats]:
    acc, w = combine(tr, pf)
    daily = daily_pnl(acc, w, first_day, end_day)
    s = dollar_stats(acc, w, daily, first_day, capital, limits)
    s["engine"] = _stats(acc, w, end_day - first_day)
    s["sharpe"] = annual_sr(daily)
    s["max_drawdown_pct"] = max_drawdown(daily)
    s["signals"] = len(tr)
    coins: dict[str, float] = {}
    if len(acc):
        by = np.bincount(acc.symbol, weights=w * acc.net, minlength=len(symbols))
        coins = {symbols[k]: float(by[k] * capital) for k in np.unique(acc.symbol)}
    s["coins_usd"] = dict(sorted(coins.items(), key=lambda kv: -kv[1]))
    reasons = np.bincount(acc.reason.astype(np.int64), minlength=4) if len(acc) else np.zeros(4)
    s["exits"] = dict(zip(("kâr al", "zarar kes", "süre", "veri boşluğu"), reasons.tolist(),
                          strict=True))  # fmt: skip
    return s, daily


def run_scalper(
    prereg: Prereg,
    prereg_sha: str,
    root: Path,
    repo: Path,
    *,
    final: bool = False,
    exploratory: bool = False,
    workers: int | None = None,
) -> dict[str, Any]:
    configs: dict[str, dict[str, Any]] = prereg.fixed["configs"]
    settings: dict[str, Setting] = {
        str(n): ScalperParams.of(configs[str(n)], prereg.fixed) for n in prereg.grid["config"]
    }
    return run_selection(
        prereg, prereg_sha, root, repo, settings, exploratory=exploratory, workers=workers
    )


def run_selection(
    prereg: Prereg,
    prereg_sha: str,
    root: Path,
    repo: Path,
    settings: dict[str, Setting],
    *,
    exploratory: bool = False,
    workers: int | None = None,
) -> dict[str, Any]:
    """``data_start`` … ``dev_end``: development (every setting, one chosen by the fixed rule:
    the best annual Sharpe among those with ≥ ``min_trades``); ``dev_end`` … ``lockbox_end``:
    the chosen setting once. Sizing: fixed size (``notional_mult``) or risk-based
    (``risk_per_trade``), as the recent test."""
    fx = prereg.fixed
    wk = workers if workers is not None else 4
    configs: dict[str, dict[str, Any]] = fx["configs"]
    names = list(settings)
    costs = costs_of(prereg)
    sizing = Sizing.of(fx)
    pf = sizing.portfolio(fx)
    capital = float(fx.get("capital_usd", 5000.0))
    limits = [float(x) for x in fx.get("loss_day_limits_usd", [150, 1000])]
    min_trades = int(fx.get("min_trades", 300))
    lake = root / "lake" / "binance_vision_um" / "klines_1m"
    symbols = sorted(p.name.split("=", 1)[1] for p in lake.glob("symbol=*USDC") if p.is_dir())
    dev0, dev1, test1 = prereg.data_start, prereg.dev_end, prereg.lockbox_end
    months = month_range(dev0[:7], _iso(_day(test1) - 1)[:7])
    rows = eligible_rows(
        root, symbols, months, float(fx.get("min_qv_day", 2e7)), int(fx.get("top", 10)), wk
    )
    warm = _iso(_day(dev0) - 10)
    commit, dirty = git_state(repo)
    exploratory = exploratory or dirty
    dsha = data_sha(repo, prereg.data_manifests or None)
    ledger = Ledger(repo)
    base = {
        "hypothesis": prereg.id,
        "version": prereg.version,
        "prereg_sha": prereg_sha,
        "commit": commit,
        "exploratory": exploratory,
        "data_sha": dsha,
    }
    # 1) development: every setting
    trades, syms = simulate(root, rows, warm, dev1, settings, costs, wk)
    d0, d1 = _day(dev0), _day(dev1)
    dev: dict[str, Any] = {}
    for n in names:
        s, daily = _evaluate(trades[n], pf, d0, d1, capital, limits, syms)
        s["params"] = asdict(settings[n])  # type: ignore[call-overload]
        s["label"] = settings[n].label()
        dev[n] = s
        period = f"{dev0}..{dev1}"
        ledger.record_trial(
            base | {"period": period},
            trial_id(prereg.id, prereg.version, {"config": n, "phase": "dev"}, dsha, period),
            {"config": n, "phase": "dev", **configs[n]},
            np.arange(d0, d1, dtype=np.int64),
            daily,
            {"sharpe": round(s["sharpe"], 3), "trades": int(s["engine"].get("trades", 0))},
        )
        log.info("selection_dev_done", config=n, sharpe=round(s["sharpe"], 2))
    ranked = [n for n in names if dev[n]["engine"].get("trades", 0) >= min_trades]
    chosen = max(ranked, key=lambda n: dev[n]["sharpe"]) if ranked else None
    test: dict[str, Any] | None = None
    if chosen is not None:
        # 2) the chosen setting once on the test period
        t_trades, t_syms = simulate(
            root, rows, _iso(_day(dev1) - 10), test1, {chosen: settings[chosen]}, costs, wk
        )
        t0, t1 = _day(dev1), _day(test1)
        test, daily = _evaluate(t_trades[chosen], pf, t0, t1, capital, limits, t_syms)
        test["params"] = asdict(settings[chosen])  # type: ignore[call-overload]
        test["label"] = settings[chosen].label()
        test["outcome"] = outcome(test, fx)
        period = f"{dev1}..{test1}"
        ledger.record_trial(
            base | {"period": period},
            trial_id(prereg.id, prereg.version, {"config": chosen, "phase": "test"}, dsha, period),
            {"config": chosen, "phase": "test", **configs[chosen]},
            np.arange(t0, t1, dtype=np.int64),
            daily,
            {"avg_day_usd": round(test["avg_day_usd"], 2), "trades": len(t_trades[chosen])},
        )
    return {
        "hypothesis": prereg.id,
        "title": prereg.title,
        "strategy": prereg.strategy,
        "version": prereg.version,
        "prereg_sha": prereg_sha,
        "commit": commit,
        "exploratory": exploratory,
        "data_sha": dsha,
        "dev_period": f"{dev0}..{dev1}",
        "test_period": f"{dev1}..{test1}",
        "verdict": test["outcome"] if test else "SONUCSUZ",
        "dev": dev,
        "chosen": chosen,
        "test": test,
        "capital_usd": capital,
        "portfolio": asdict(pf),
        "sizing": {
            "trade_usd": capital * sizing.notional_mult,
            "max_positions": sizing.max_positions,
            "risk_per_trade": sizing.risk_per_trade,
        },
        "targets": {
            "usd_day": float(fx.get("target_usd_day", 10.0)),
            "max_drawdown_usd": float(fx.get("max_drawdown_usd", 750.0)),
            "trades_day": float(fx.get("min_trades_day", 5.0)),
        },
        "universe_months": {m: [s for mm, _, s in rows if mm == m] for m in months},
        "program_trials": ledger.count(),
    }


SCALPER_METHOD = [
    "- Her 5 dakikada, pozisyonu olmayan her uygun USDC kontratında (önceki ay günde ≥ 20 "
    "milyon $ işlem, en az bir tam aydır listeli, en hacimli 10): kapanışın d altına alış, d "
    "üstüne satış yalnız-maker limit emir, d = a·σ·√15. 5 dk bekler; fiyat ötesine geçen ilk "
    "taraf dolar, öbürü iptal. Aynı mumda ikisi birden geçilirse mumun ters gittiği taraf "
    "sayılır (karamsar).",
    "- Kâr al: maker limit, fiyat ötesine geçince dolar, giriş mumunda sayılmaz. Zarar kes: "
    "stop-market, taker %0,04 + iki kat kayma; aynı mumda ikisi de varsa zarar kes sayılır. "
    "Süre dolunca maker limit, 5 dk'da dolmazsa piyasa emri. Funding gerçek oranlarla.",
    "- Durgun piyasa filtresi: son 60 dakikanın verimlilik oranı < 0,3 (yön yok) ve oynaklığı "
    "normalin 1,5 katından az.",
]


def scalper_markdown(doc: dict[str, Any]) -> str:
    return selection_markdown(doc, SCALPER_METHOD)


def _label(s: dict[str, Any]) -> str:
    if "label" in s:
        return str(s["label"])
    p = s["params"]  # G1 documents (written before labels existed)
    quiet = "durgun filtre" if p.get("quiet") else "filtre yok"
    return f"a {num(p['a'], 1)}, kâr al {num(p['tp'], 1)}·d, {quiet}"


def selection_markdown(doc: dict[str, Any], method: list[str]) -> str:
    pf, tg = doc["portfolio"], doc["targets"]
    sz = doc.get("sizing") or {}
    if sz.get("risk_per_trade") or not sz:
        size_text = (
            f"işlem başı risk %{pf['risk_per_trade'] * 100:.1f}, coin başına ≤ "
            f"{num(pf['cap_symbol'], 1)}×, toplam ≤ {num(pf['cap_gross'], 1)}×"
        )
    else:
        size_text = f"her işlem {usd(sz['trade_usd'])}"
    lines = [
        f"# {doc['hypothesis']} — {doc['title']}",
        "",
        f"Geliştirme {doc['dev_period'].replace('..', ' – ')} · test "
        f"{doc['test_period'].replace('..', ' – ')} · sermaye {usd(doc['capital_usd'])}, "
        f"{size_text}, en fazla {pf['max_positions']} pozisyon · gerçek USDC mumları · kod "
        f"`{doc['commit'][:7]}`" + (" · **keşif koşusu (sayılmaz)**" if doc["exploratory"] else ""),
        "",
        f"Hedef (ön-kayıtlı): günde ortalama ≥ {usd(tg['usd_day'])}, en büyük düşüş ≤ "
        f"{usd(tg['max_drawdown_usd'])}, günde ≥ {tg['trades_day']:.0f} işlem.",
        "",
        "## Sonuç",
        "",
    ]
    t = doc["test"]
    if t is None:
        lines.append("Geliştirme döneminde yeterli işlem yapan ayar yok: **SONUÇSUZ**.")
    else:
        lines.append(
            f"Seçilen ayar **{doc['chosen']}** ({_label(t)}) test döneminde: "
            f"**{OUTCOME_TEXT[t['outcome']]}** — günde ortalama {usd(t['avg_day_usd'])}, günde "
            f"{num(t['trades_per_day'], 1)} işlem, kazanan işlem "
            f"{pct(t['engine'].get('win_rate'), 0)}, en büyük düşüş {usd(t['max_drawdown_usd'])}."
        )
    lines += [
        "",
        "## Geliştirme dönemi (bütün ayarlar)",
        "",
        "| Ayar | Kural | Yıllık Sharpe | Günlük ort. | En büyük düşüş | Günde işlem | Kazanan |",
        "|---|---|---|---|---|---|---|",
    ]
    for n, s in doc["dev"].items():
        mark = " ←" if n == doc["chosen"] else ""
        lines.append(
            f"| {n}{mark} | {_label(s)} | {num(s['sharpe'])} | {usd(s['avg_day_usd'])} | "
            f"{usd(s['max_drawdown_usd'])} | {num(s['trades_per_day'], 1)} | "
            f"{pct(s['engine'].get('win_rate'), 0)} |"
        )
    if t is not None:
        lines += ["", f"## Test dönemi: {doc['chosen']}", "", "| | |", "|---|---|"]
        rows_t = [
            ("Günlük ortalama kâr", usd(t["avg_day_usd"])),
            ("Günlük medyan", usd(t["median_day_usd"])),
            ("Kârlı gün oranı", pct(t["positive_days"], 0)),
            ("En iyi / en kötü gün", f"{usd(t['best_day_usd'])} / {usd(t['worst_day_usd'])}"),
            ("Toplam", usd(t["total_usd"])),
            ("En büyük düşüş", f"{usd(t['max_drawdown_usd'])} ({pct(t['max_drawdown_pct'], 0)})"),
            ("Günde işlem", num(t["trades_per_day"], 1)),
            ("Kazanan işlem oranı", pct(t["engine"].get("win_rate"), 0)),
            ("İşlem başı ortalama net", usd(t["avg_trade_usd"], 2)),
            ("Çıkışlar", ", ".join(f"{k} {v}" for k, v in t["exits"].items())),
            ("Ödenen komisyon / kayma", f"{usd(t['fees_usd'])} / {usd(t['slippage_usd'])}"),
            ("Yıllık Sharpe", num(t["sharpe"])),
            ("İstatistiksel güven (t)", num(t["t_nw"])),
        ]
        lines += [f"| {a} | {b} |" for a, b in rows_t]
        lines += ["", "| Ay | Kâr/zarar | İşlem |", "|---|---|---|"]
        lines += [f"| {m} | {usd(r['usd'])} | {r['trades']} |" for m, r in t["monthly"].items()]
        if t["coins_usd"]:
            lines += [
                "",
                "Coinler: "
                + ", ".join(
                    f"{s.removesuffix('USDC')} {usd(v)}" for s, v in t["coins_usd"].items()
                ),
            ]
    lines += [
        "",
        "## Yöntem",
        "",
        *method,
        "- Seçim kuralı koşudan önce yazıldı: geliştirme döneminde en az 300 işlem yapanlar "
        "arasında en yüksek yıllık Sharpe; test dönemi yalnız o ayarla bir kez koşuldu.",
        f"- Ön-kayıt sha256 `{doc['prereg_sha'][:12]}`, veri `{doc['data_sha']}`, deneme "
        f"defterindeki toplam {doc['program_trials']}.",
        "",
    ]
    return "\n".join(lines)
