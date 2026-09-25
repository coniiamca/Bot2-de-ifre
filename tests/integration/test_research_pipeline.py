"""Research pipeline end to end against a fake archive (S3 listing with paging, MD5 ETags):
universe → fetch → point-in-time panel → pre-registered run → ledger and report."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import subprocess
import zipfile
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

np = pytest.importorskip("numpy")

from quanta.research.data import load_market  # noqa: E402
from quanta.research.fetch import (  # noqa: E402
    M1_EXTRA,
    MINUTE,
    USDC,
    fetch_dataset,
    symbol_windows,
    usdc_counterparts,
    usdc_universe,
)
from quanta.research.intraday_runner import run_intraday  # noqa: E402
from quanta.research.ledger import Ledger  # noqa: E402
from quanta.research.minute import load_bars  # noqa: E402
from quanta.research.ml_runner import run_ml  # noqa: E402
from quanta.research.prereg import Prereg, PreregError, load_prereg  # noqa: E402
from quanta.research.report import (  # noqa: E402
    intraday_markdown,
    ml_markdown,
    trend_markdown,
    write_report,
)
from quanta.research.runner import run_trend  # noqa: E402
from quanta.research.universe import (  # noqa: E402
    build_universe,
    month_range,
    read_universe,
    subset_universe,
)

PREFIX = "data/futures/um"
MONTHS = ["2020-01", "2020-02", "2020-03", "2020-04", "2020-05"]


class FakeS3Archive:
    """Serves files under /data/… and an S3-style listing at /listing (2 keys per page)."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.port = 0
        self.page = 2

    def add(self, key: str, data: bytes) -> None:
        self.files[key] = data

    async def handler(self, request: web.Request) -> web.Response:
        if request.path == "/listing":
            return self._listing(request)
        key = request.path.lstrip("/")
        if key not in self.files:
            raise web.HTTPNotFound()
        return web.Response(body=self.files[key])

    def _listing(self, request: web.Request) -> web.Response:
        prefix = request.query.get("prefix", "")
        marker = request.query.get("marker", "")
        delim = request.query.get("delimiter")
        keys = sorted(k for k in self.files if k.startswith(prefix))
        entries: list[tuple[str, str]] = []  # (sort key, xml)
        seen: set[str] = set()
        for k in keys:
            rest = k[len(prefix) :]
            if delim and delim in rest:
                cp = prefix + rest.split(delim, 1)[0] + delim
                if cp not in seen:
                    seen.add(cp)
                    entries.append((cp, f"<CommonPrefixes><Prefix>{cp}</Prefix></CommonPrefixes>"))
                continue
            md5 = hashlib.md5(self.files[k], usedforsecurity=False).hexdigest()
            entries.append(
                (
                    k,
                    f"<Contents><Key>{k}</Key><LastModified>2021-01-01T00:00:00.000Z"
                    f"</LastModified><ETag>&quot;{md5}&quot;</ETag><Size>{len(self.files[k])}"
                    "</Size></Contents>",
                )
            )
        entries = [e for e in sorted(entries) if e[0] > marker]
        page, more = entries[: self.page], len(entries) > self.page
        nxt = f"<NextMarker>{page[-1][0]}</NextMarker>" if more and delim else ""
        body = (
            '<?xml version="1.0" encoding="UTF-8"?><ListBucketResult>'
            f"<IsTruncated>{'true' if more else 'false'}</IsTruncated>{nxt}"
            + "".join(x for _, x in page)
            + "</ListBucketResult>"
        )
        return web.Response(text=body, content_type="application/xml")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def _zip(name: str, rows: list[str], header: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, header + "\n" + "\n".join(rows) + "\n")
    return buf.getvalue()


KL_HEADER = (
    "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
    "taker_buy_volume,taker_buy_quote_volume,ignore"
)


def _ms(d: datetime) -> int:
    return int(d.timestamp() * 1000)


def _month_days(m: str) -> list[datetime]:
    d = datetime.fromisoformat(m + "-01").replace(tzinfo=UTC)
    out = []
    while d.strftime("%Y-%m") == m:
        out.append(d)
        d += timedelta(days=1)
    return out


def populate(fa: FakeS3Archive) -> None:
    rng = np.random.default_rng(3)
    # AAA biggest; BBB listed late (needs 60 days); CCC delisted after 2020-03;
    # stablecoin and dated contracts must be ignored
    # DDD: listed mid-month (2020-01-20), tiny volume — only the 60-day rule looks at it
    specs = {
        "AAAUSDT": (MONTHS, 1e9, 1),
        "BBBUSDT": (MONTHS[1:], 5e9, 1),
        "CCCUSDT": (MONTHS[:3], 2e8, 1),
        "DDDUSDT": (MONTHS, 1e6, 20),
        "USDCUSDT": (MONTHS, 9e9, 1),
        "XYZUSDT_200626": (MONTHS, 9e9, 1),
    }
    for sym, (months, qv, first_day) in specs.items():
        px = 100.0
        for m in months:
            days = [d for d in _month_days(m) if m != months[0] or d.day >= first_day]
            daily = [
                f"{_ms(d)},1,1,1,1,1,{_ms(d) + 86_399_999},{qv / len(days):.0f},10,0,0,0"
                for d in days
            ]
            key = f"{PREFIX}/monthly/klines/{sym}/1d/{sym}-1d-{m}.zip"
            fa.add(key, _zip("x.csv", daily, KL_HEADER))
            hours, funding, prem = [], [], []
            for d in days:
                for h in range(24):
                    t = d + timedelta(hours=h)
                    r = rng.normal(0, 0.01)
                    o, px = px, px * (1 + r)
                    hi, lo = max(o, px) * 1.001, min(o, px) * 0.999
                    hours.append(
                        f"{_ms(t)},{o:.4f},{hi:.4f},{lo:.4f},{px:.4f},10,{_ms(t) + 3_599_999},"
                        f"{10 * px:.4f},5,0,0,0"
                    )
                    prem.append(
                        f"{_ms(t)},0.0001,0.0002,0,0.0001,0,{_ms(t) + 3_599_999},0,720,0,0,0"
                    )
                    if h % 8 == 0:
                        funding.append(f"{_ms(t) + 15},8,0.0001")
            fa.add(
                f"{PREFIX}/monthly/klines/{sym}/1h/{sym}-1h-{m}.zip",
                _zip("x.csv", hours, KL_HEADER),
            )
            fa.add(
                f"{PREFIX}/monthly/premiumIndexKlines/{sym}/1h/{sym}-1h-{m}.zip",
                _zip("x.csv", prem, KL_HEADER),
            )
            fa.add(
                f"{PREFIX}/monthly/fundingRate/{sym}/{sym}-fundingRate-{m}.zip",
                _zip("x.csv", funding, "calc_time,funding_interval_hours,last_funding_rate"),
            )


@pytest.fixture
async def archive() -> AsyncIterator[FakeS3Archive]:
    fa = FakeS3Archive()
    populate(fa)
    app = web.Application()
    app.router.add_get("/{tail:.*}", fa.handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    fa.port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    yield fa
    await runner.cleanup()


def prereg(universe_file: str) -> Prereg:
    return Prereg(
        id="HT",
        version=1,
        title="test",
        mechanism="m",
        falsification="f",
        universe_file=universe_file,
        data_start="2020-01-01",
        warmup_days=60,
        dev_end="2020-05-01",
        lockbox_end="2020-06-01",
        grid={"lookback_h": [24, 72], "rebalance_h": [24], "signal": ["sign"]},
        fixed={"min_history_h": 48, "vol_halflife_h": 24},
        stress_windows=[("2020-04-01", "2020-04-10", "test window")],
    )


async def test_universe_fetch_panel_and_run(tmp_path: Path, archive: FakeS3Archive) -> None:
    root, repo = tmp_path / "data", tmp_path / "repo"
    ucsv = repo / "research/universe/u.csv"
    kw: dict[str, Any] = {"listing_url": archive.url + "/listing", "base_url": archive.url}
    rows = await build_universe(
        root, "2020-03", "2020-05", ucsv, tmp_path / "m1.csv.gz", top_n=2, **kw
    )
    by_month: dict[str, list[str]] = {}
    for m, _, s, _ in rows:
        by_month.setdefault(m, []).append(s)
    # March: BBB has only 29 days of history → not yet; USDC and dated contracts never
    assert by_month["2020-03"] == ["AAAUSDT", "CCCUSDT"]
    # May: ranked by April volume; CCC (delisted in April) has no April volume
    assert by_month["2020-05"] == ["BBBUSDT", "AAAUSDT"]
    with ucsv.open() as fh:
        assert next(csv.reader(fh)) == ["month", "rank", "symbol", "prev_month_quote_volume"]

    universe = read_universe(ucsv)
    used = await fetch_dataset(root, universe, tmp_path / "m2.csv.gz", metrics_symbols=(), **kw)
    assert used and all(u.result.path is not None for u in used)

    m = load_market(root, universe, "2020-01-01", "2020-06-01", metrics=False)
    m.check_point_in_time()
    assert m.symbols == ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
    march = (m.grid >= int(datetime(2020, 3, 1, tzinfo=UTC).timestamp()) * 10**9) & (
        m.grid < int(datetime(2020, 4, 1, tzinfo=UTC).timestamp()) * 10**9
    )
    assert m.universe[march][:, 2].all() and not m.universe[march][:, 1].any()
    assert not m.tradable[-24:, 2].any()  # CCC delisted
    hours = (m.grid // (3600 * 10**9)) % 24
    fund = m.funding[:, 0] != 0
    assert set(hours[fund]) == {0, 8, 16}  # charged at the funding hour (calc_time + 15 ms)

    pr = prereg("research/universe/u.csv")
    doc = run_trend(pr, "sha", root, repo)
    assert doc["verdict"] in ("GECTI", "ELENDI") and len(doc["family"]["trials"]) == 2
    assert {g["name"] for g in doc["gates"]} >= {"cpcv", "dsr", "pbo", "t_nw", "alfa"}
    ledger = Ledger(repo)
    assert ledger.count("HT") == 2
    for e in ledger.entries():
        assert (repo / e["returns_file"]).exists()
    again = run_trend(pr, "sha", root, repo)
    assert ledger.count("HT") == 2  # same trials: recorded once
    strip = {"program_trials"}
    assert {k: v for k, v in again.items() if k not in strip} == {
        k: v for k, v in doc.items() if k not in strip
    }  # deterministic
    out = write_report(repo, doc, trend_markdown(doc))
    assert "Karar:" in out.read_text() and json.loads(out.with_suffix(".json").read_text())
    if doc["verdict"] == "ELENDI":
        with pytest.raises(RuntimeError, match="lockbox stays closed"):
            run_trend(pr, "sha", root, repo, final=True)
        assert not ledger.lockbox_opened("HT")

    # a narrower universe, decided after seeing HT: the same ranking cut at 1 ...
    u1 = repo / "research/universe/u1.csv"
    assert subset_universe(ucsv, u1, 1) == 3
    rebuilt = tmp_path / "u1_rebuilt.csv"
    await build_universe(root, "2020-03", "2020-05", rebuilt, tmp_path / "m3.csv.gz", top_n=1, **kw)
    assert u1.read_text() == rebuilt.read_text()
    # ... and HT's recorded trials count in its luck bar
    narrow = pr.model_copy(update={"id": "HT2", "universe_file": "research/universe/u1.csv"})
    alone = run_trend(narrow, "sha", root, repo)
    counted = run_trend(
        narrow.model_copy(update={"id": "HT3", "prior_trials": ["HT"]}), "sha", root, repo
    )
    assert counted["family"]["n_prior"] == 2 and counted["prior"]["trials"] == 2
    assert counted["prior"]["hypotheses"] == ["HT"] and alone["prior"] is None
    assert counted["best"] == alone["best"]
    assert counted["family"]["dsr"] <= alone["family"]["dsr"]
    assert "Önceki denemelerle" in trend_markdown(counted)
    with pytest.raises(RuntimeError, match="no recorded trials"):
        run_trend(
            narrow.model_copy(update={"id": "HT4", "prior_trials": ["NOPE"]}), "sha", root, repo
        )


async def test_history_rule_uses_the_real_listing_day(
    tmp_path: Path, archive: FakeS3Archive
) -> None:
    """A universe built from a later start must still read the first listed month: DDD was
    listed on 2020-01-20, so it has 60 days of history only from 2020-03-20."""
    kw: dict[str, Any] = {"listing_url": archive.url + "/listing", "base_url": archive.url}
    rows = await build_universe(
        tmp_path / "d",
        "2020-03",
        "2020-05",
        tmp_path / "u.csv",
        tmp_path / "m.csv.gz",
        top_n=10,
        **kw,
    )
    months = {m for m, _, s, _ in rows if s == "DDDUSDT"}
    assert months == {"2020-04", "2020-05"}


def add_minutes(fa: FakeS3Archive, symbols: dict[str, list[str]]) -> None:
    """1-minute klines (random walk with occasional bursts) for the given months."""
    rng = np.random.default_rng(7)
    for sym, months in symbols.items():
        px = 100.0
        for m in months:
            t0 = _ms(datetime.fromisoformat(m + "-01").replace(tzinfo=UTC))
            n = len(_month_days(m)) * 1440
            r = rng.normal(0, 0.001, n)
            burst = rng.random(n) < 0.01
            r[burst] *= 8
            c = px * np.exp(np.cumsum(r))
            o = np.concatenate([[px], c[:-1]])
            px = float(c[-1])
            h, lo = np.maximum(o, c) * 1.0002, np.minimum(o, c) * 0.9998
            v = np.where(burst, 50.0, 5.0)
            tb = v * rng.uniform(0.2, 0.8, n)
            rows = [
                f"{t0 + 60_000 * i},{o[i]:.5f},{h[i]:.5f},{lo[i]:.5f},{c[i]:.5f},{v[i]},"
                f"{t0 + 60_000 * i + 59_999},{v[i] * c[i]:.3f},3,{tb[i]:.4f},0,0"
                for i in range(n)
            ]
            fa.add(
                f"{PREFIX}/monthly/klines/{sym}/1m/{sym}-1m-{m}.zip", _zip("x.csv", rows, KL_HEADER)
            )


async def test_intraday_run_end_to_end(tmp_path: Path, archive: FakeS3Archive) -> None:
    root, repo = tmp_path / "data", tmp_path / "repo"
    kw: dict[str, Any] = {"listing_url": archive.url + "/listing", "base_url": archive.url}
    ucsv = repo / "research/universe/u.csv"
    await build_universe(root, "2020-03", "2020-04", ucsv, tmp_path / "m1.csv.gz", top_n=2, **kw)
    universe = read_universe(ucsv)
    add_minutes(archive, {"AAAUSDT": ["2020-02", "2020-03", "2020-04"], "CCCUSDT": ["2020-03"]})
    await fetch_dataset(root, universe, tmp_path / "m2.csv.gz", metrics_symbols=(), **kw)
    used = await fetch_dataset(
        root,
        universe,
        tmp_path / "m3.csv.gz",
        datasets=MINUTE,
        pad_before=1,
        pad_after=0,
        metrics_symbols=(),
        **kw,
    )
    assert {u.listed.key.rsplit("/", 1)[-1] for u in used} == {
        "AAAUSDT-1m-2020-02.zip",
        "AAAUSDT-1m-2020-03.zip",
        "AAAUSDT-1m-2020-04.zip",
        "CCCUSDT-1m-2020-03.zip",
    }
    b = load_bars(root, universe, "AAAUSDT", "2020-02-01", "2020-05-01")
    assert b is not None and len(b) == (29 + 31 + 30) * 1440
    assert (b.rank[: 29 * 1440] == 0).all() and (b.rank[29 * 1440 :] > 0).all()
    assert np.all(np.diff(b.t) == 60 * 10**9) and b.funding_t.size > 0
    pr = Prereg(
        id="HI",
        version=1,
        title="intraday test",
        mechanism="m",
        falsification="f",
        strategy="flow",
        universe_file="research/universe/u.csv",
        data_start="2020-02-01",
        warmup_days=29,
        dev_end="2020-05-01",
        lockbox_end="2020-06-01",
        grid={"k": [5, 15], "e": [2.0], "hold": [15], "entry": ["market"], "pool": [1, 2]},
        fixed={"vol_halflife_min": 1440, "warmup_days": 5, "limit_valid": 5},
        validation={"hold_days": 1, "lookback_days": 1},
        stress_windows=[("2020-03-10", "2020-03-20", "test")],
    )
    doc = run_intraday(pr, "sha", root, repo, workers=1)
    assert doc["verdict"] in ("GECTI", "ELENDI", "SONUCSUZ") and len(doc["family"]["trials"]) == 4
    assert {g["name"] for g in doc["gates"]} >= {"dsr", "gecikme", "islem_sayisi", "alfa"}
    assert set(doc["pools"]) == {"1", "2"} and doc["best"]["trades"]["trades"] > 0
    assert Ledger(repo).count("HI") == 4
    again = run_intraday(pr, "sha", root, repo, workers=2)  # processes: same answer
    strip = {"program_trials"}
    assert {k: v for k, v in again.items() if k not in strip} == {
        k: v for k, v in doc.items() if k not in strip
    }
    md = intraday_markdown(doc)
    assert "Coin havuzu" in md and "Karar:" in md

    # H5 path: quarter-hour opening imbalance from the minute bars, hourly weights
    h5 = Prereg(
        id="HQ",
        version=1,
        title="burst test",
        mechanism="m",
        falsification="f",
        strategy="burst_imbalance",
        universe_file="research/universe/u.csv",
        universe_label="test coins",
        data_start="2020-02-01",
        warmup_days=29,
        dev_end="2020-05-01",
        lockbox_end="2020-06-01",
        grid={"lookback_h": [1, 4], "rebalance_h": [4], "signal": ["sign"]},
        fixed={"min_history_h": 48, "vol_halflife_h": 24},
        subperiods=[("2020-03-01", "2020-04-01", "March"), ("2020-04-01", "2020-05-01", "April")],
    )
    hq = run_trend(h5, "sha", root, repo)
    assert hq["verdict"] in ("GECTI", "ELENDI") and len(hq["family"]["trials"]) == 2
    assert set(hq["subperiods"]) == {"March", "April"} and hq["universe_label"] == "test coins"
    text = trend_markdown(hq)
    assert "Alt dönemler" in text and "test coins" in text


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_prereg_must_be_committed_and_unchanged(tmp_path: Path) -> None:
    repo = tmp_path
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    path = repo / "research/prereg/HT.yaml"
    path.parent.mkdir(parents=True)
    body = prereg("u.csv").model_dump(mode="json")
    path.write_text(json.dumps(body))
    with pytest.raises(PreregError, match="not committed"):
        load_prereg(path)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "prereg")
    p, sha = load_prereg(path)
    assert p.id == "HT" and sha == hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_text(json.dumps(body | {"version": 2}))
    with pytest.raises(PreregError, match="local changes"):
        load_prereg(path)
    assert load_prereg(path, require_committed=False)[0].version == 2
    assert date.fromisoformat(p.dev_end) < date.fromisoformat(p.lockbox_end)


METRICS_HEADER = (
    "create_time,symbol,sum_open_interest,sum_open_interest_value,"
    "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,count_long_short_ratio,"
    "sum_taker_long_short_vol_ratio"
)


def add_model_extras(fa: FakeS3Archive, symbols: dict[str, list[str]]) -> None:
    """5-minute premium index klines and daily 5-minute metrics for model M1."""
    rng = np.random.default_rng(11)
    for sym, months in symbols.items():
        oi = 1000.0
        for m in months:
            prem = []
            for d in _month_days(m):
                rows = []
                for k in range(288):
                    t = d + timedelta(minutes=5 * k)
                    p = rng.normal(0, 2e-4)
                    prem.append(f"{_ms(t)},{p},{p},{p},{p},0,{_ms(t) + 299_999},0,60,0,0,0")
                    oi *= float(np.exp(rng.normal(0, 0.002)))
                    ratio = float(np.exp(rng.normal(0, 0.05)))
                    rows.append(
                        f"{t:%Y-%m-%d %H:%M:%S},{sym},{oi:.3f},{oi * 100:.3f},{ratio:.5f},"
                        f"{ratio:.5f},{ratio:.5f},{ratio:.5f}"
                    )
                fa.add(
                    f"{PREFIX}/daily/metrics/{sym}/{sym}-metrics-{d:%Y-%m-%d}.zip",
                    _zip("x.csv", rows, METRICS_HEADER),
                )
            fa.add(
                f"{PREFIX}/monthly/premiumIndexKlines/{sym}/5m/{sym}-5m-{m}.zip",
                _zip("x.csv", prem, KL_HEADER),
            )


async def test_model_run_end_to_end(tmp_path: Path, archive: FakeS3Archive) -> None:
    pytest.importorskip("lightgbm")
    root, repo = tmp_path / "data", tmp_path / "repo"
    kw: dict[str, Any] = {"listing_url": archive.url + "/listing", "base_url": archive.url}
    ucsv = repo / "research/universe/u.csv"
    await build_universe(root, "2020-03", "2020-04", ucsv, tmp_path / "m1.csv.gz", top_n=2, **kw)
    universe = read_universe(ucsv)
    windows = symbol_windows(universe, 1, 0)
    months = {s: month_range(lo, hi) for s, (lo, hi) in windows.items()}
    add_minutes(archive, months | {"AAAUSDC": ["2020-02", "2020-03", "2020-04"]})
    for m in ("2020-02", "2020-03", "2020-04"):
        funding = [f"{_ms(d) + 8 * 3_600_000 * k + 15},8,0.0001" for d in _month_days(m)
                   for k in range(3)]  # fmt: skip
        archive.add(
            f"{PREFIX}/monthly/fundingRate/AAAUSDC/AAAUSDC-fundingRate-{m}.zip",
            _zip("x.csv", funding, "calc_time,funding_interval_hours,last_funding_rate"),
        )
    add_model_extras(archive, months)
    symbols = sorted(windows)
    await fetch_dataset(root, universe, tmp_path / "h.csv.gz", metrics_symbols=(), **kw)
    await fetch_dataset(
        root, universe, tmp_path / "m.csv.gz", datasets=MINUTE, pad_before=1, pad_after=0,
        metrics_symbols=(), **kw,
    )  # fmt: skip
    used = await fetch_dataset(
        root, universe, tmp_path / "x.csv.gz", datasets=M1_EXTRA, pad_before=1, pad_after=0,
        metrics_symbols=tuple(symbols), metrics_pad=(1, 0), metrics_start=date(2020, 1, 1),
        **kw,
    )  # fmt: skip
    got = {u.listed.key.rsplit("/", 1)[-1] for u in used}
    # metrics only for each symbol's own months (+1 before), never the whole range
    assert "CCCUSDT-metrics-2020-02-10.zip" in got and "CCCUSDT-metrics-2020-04-10.zip" not in got
    pairs = await usdc_counterparts(symbols, listing_url=kw["listing_url"])
    assert pairs == {"AAAUSDT": "AAAUSDC"}
    await fetch_dataset(
        root, usdc_universe(pairs, "2020-02", "2020-04"), tmp_path / "u.csv.gz", datasets=USDC,
        pad_before=0, pad_after=0, metrics_symbols=(), **kw,
    )  # fmt: skip
    pr = Prereg(
        id="HM",
        version=1,
        title="model test",
        mechanism="m",
        falsification="f",
        strategy="ml",
        universe_file="research/universe/u.csv",
        data_start="2020-02-01",
        dev_end="2020-05-01",
        lockbox_end="2020-06-01",
        grid={"horizon": [15, 60], "q": [0.01], "model": ["ridge", "lgbm"]},
        fixed={
            "top": 2,
            "ml_start": "2020-04",
            "usdc_first_month": "2020-02",
            "usdc_from": "2020-04",
            "usdc_min_qv_day": 0,
            "usdc_check_start": "2020-04-01",
            "recent_start": "2020-04-10",
            "prior_trial_count": 10,
            "model": {
                "train_days": 20,
                "calib_days": 5,
                "rounds": 10,
                "min_train_rows": 1000,
                "lgbm": {"min_data_in_leaf": 100, "num_threads": 2},
            },
        },
        costs={"fee_bps": 4.0, "maker_bps": 0.0, "slip_top2_bps": 1.5, "slip_rest_bps": 4.5},
        validation={"hold_days": 1, "lookback_days": 1},
        stress_windows=[("2020-04-10", "2020-04-20", "test")],
    )
    doc = run_ml(pr, "sha", root, repo, workers=1)
    assert doc["verdict"] in ("GECTI", "ELENDI", "SONUCSUZ") and len(doc["family"]["trials"]) == 4
    assert doc["family"]["n_prior"] == 10
    assert {g["name"] for g in doc["gates"]} >= {"dsr", "usdc_kontrol", "son_donem", "gecikme"}
    assert doc["usdc_check"]["signals"] > 0 and doc["best"]["trades"]["trades"] > 0
    # from 2020-04 only coins with an eligible USDC contract trade: AAA only
    assert doc["symbols"] == 1
    assert Ledger(repo).count("HM") == 4
    again = run_ml(pr, "sha", root, repo, workers=2)  # processes: same answer
    strip = {"program_trials"}
    assert {k: v for k, v in again.items() if k not in strip} == {
        k: v for k, v in doc.items() if k not in strip
    }
    md = ml_markdown(doc)
    assert "Gerçek USDC" in md and "Karar:" in md
    timing = run_ml(pr, "sha", root, repo, workers=1, shuffle_seed=0)
    assert timing["exploratory"] and timing["timing_run"] and Ledger(repo).count("HM") == 4
    if doc["verdict"] != "GECTI":
        with pytest.raises(RuntimeError, match="lockbox stays closed"):
            run_ml(pr, "sha", root, repo, workers=1, final=True)
