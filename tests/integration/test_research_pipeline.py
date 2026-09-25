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
from quanta.research.fetch import fetch_dataset  # noqa: E402
from quanta.research.ledger import Ledger  # noqa: E402
from quanta.research.prereg import Prereg, PreregError, load_prereg  # noqa: E402
from quanta.research.report import trend_markdown, write_report  # noqa: E402
from quanta.research.runner import run_trend  # noqa: E402
from quanta.research.universe import build_universe, read_universe  # noqa: E402

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
    specs = {
        "AAAUSDT": (MONTHS, 1e9),
        "BBBUSDT": (MONTHS[1:], 5e9),
        "CCCUSDT": (MONTHS[:3], 2e8),
        "USDCUSDT": (MONTHS, 9e9),
        "XYZUSDT_200626": (MONTHS, 9e9),
    }
    for sym, (months, qv) in specs.items():
        px = 100.0
        for m in months:
            days = _month_days(m)
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
