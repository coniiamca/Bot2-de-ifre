"""Tardis importer against a local fake of datasets.tardis.dev."""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from aiohttp import web

from quanta.archive import tardis as td

KEY = "test-key-123"
T0 = 1759276800_000_000  # 2025-10-01T00:00:00Z in µs

TRADES = [
    "exchange,symbol,timestamp,local_timestamp,id,side,price,amount",
    f"binance-futures,BTCUSDT,{T0 + 216_000},{T0 + 256_578},6781,buy,114013.8,0.002",
    f"binance-futures,BTCUSDT,{T0 + 583_000},{T0 + 623_467},6782,sell,114013.7,0.011",
]
LIQUIDATIONS = [  # Binance liquidations have an empty id column
    "exchange,symbol,timestamp,local_timestamp,id,side,price,amount",
    f"binance-futures,BTCUSDT,{T0 + 14_331_000},{T0 + 14_335_721},,buy,114494.1,0.014",
]
TICKER = [
    "exchange,symbol,timestamp,local_timestamp,funding_timestamp,funding_rate,"
    "predicted_funding_rate,open_interest,last_price,index_price,mark_price",
    f"binance-futures,BTCUSDT,{T0 + 1_000_000},{T0 + 1_004_000},{T0 + 28_800_000_000},"
    "0.0001,,82011.5,114010.1,114020.3,114015.2",
]
L2 = [
    "exchange,symbol,timestamp,local_timestamp,is_snapshot,side,price,amount",
    f"binance-futures,BTCUSDT,{T0},{T0 + 5_000},true,bid,114000.0,1.5",
    f"binance-futures,BTCUSDT,{T0 + 100_000},{T0 + 105_000},false,ask,114001.0,0",
]
SNAP25 = [
    "exchange,symbol,timestamp,local_timestamp,"
    + ",".join(
        f"asks[{i}].price,asks[{i}].amount,bids[{i}].price,bids[{i}].amount" for i in range(2)
    ),
    f"binance-futures,BTCUSDT,{T0},{T0 + 7_000},114001,0.5,114000,1.25,114002,0.1,113999,2",
]


def gz(lines: list[str]) -> bytes:
    return gzip.compress(("\n".join(lines) + "\n").encode(), mtime=0)


class FakeTardis:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.bad_md5: set[str] = set()
        self.hits: dict[str, int] = {}
        self.auth_seen: list[str | None] = []
        self.quota_after: int | None = None  # answer 429 once this many files were served
        self.limit_once = False  # answer the next request with 429
        self.retry_after = "16912"
        self.served = 0
        self.port = 0
        self._runner: web.AppRunner | None = None

    def add(self, exchange: str, dtype: str, day: str, symbol: str, data: bytes) -> str:
        y, m, d = day.split("-")
        rel = f"{exchange}/{dtype}/{y}/{m}/{d}/{symbol}.csv.gz"
        self.files[rel] = data
        return rel

    async def handler(self, request: web.Request) -> web.StreamResponse:
        rel = request.path.removeprefix("/v1/")
        self.hits[rel] = self.hits.get(rel, 0) + 1
        auth = request.headers.get("Authorization")
        self.auth_seen.append(auth)
        limited = self.quota_after is not None and self.served >= self.quota_after
        if limited or self.limit_once:
            self.limit_once = False
            return web.json_response(
                {"code": 277, "message": "This account has reached its data transfer limit."},
                status=429,
                headers={"Retry-After": self.retry_after},
            )
        if rel.split("/")[-2] != "01" and auth != f"Bearer {KEY}":
            return web.json_response({"code": 401, "message": "unauthorized"}, status=401)
        if rel not in self.files:
            raise web.HTTPNotFound()
        body = self.files[rel]
        md5 = "0" * 32 if rel in self.bad_md5 else hashlib.md5(body).hexdigest()
        self.served += 1
        return web.Response(
            body=body,
            content_type="text/csv",
            headers={"x-md5": f'"{md5}"', "x-dataset-size": str(len(body))},
        )

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/v1/{tail:.*}", self.handler)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"


@pytest.fixture
async def fake() -> AsyncIterator[FakeTardis]:
    f = FakeTardis()
    await f.start()
    yield f
    await f.stop()


def jobs(types: list[str], days: list[date], l2_days: tuple[date, ...] = ()) -> list[td.Job]:
    return td.plan_jobs(["binance-futures"], {"binance-futures": ["BTCUSDT"]}, days, types, l2_days)


D1 = date(2025, 10, 1)


async def test_free_day_small_types_to_parquet(tmp_path: Path, fake: FakeTardis) -> None:
    fake.add("binance-futures", "trades", "2025-10-01", "BTCUSDT", gz(TRADES))
    fake.add("binance-futures", "liquidations", "2025-10-01", "BTCUSDT", gz(LIQUIDATIONS))
    fake.add("binance-futures", "derivative_ticker", "2025-10-01", "BTCUSDT", gz(TICKER))
    rep = await td.run_import(tmp_path, jobs(list(td.SMALL_TYPES), [D1]), base_url=fake.url)
    assert rep.ok and rep.counts() == {"downloaded": 3}, rep.results
    assert all(a is None for a in fake.auth_seen)  # no key → no Authorization header

    t = pq.read_table(td.parquet_path(tmp_path, "binance-futures", "trades", D1, "BTCUSDT"))
    assert t.num_rows == 2
    assert t.schema.field("ts_exchange").type == pa.timestamp("ns", tz="UTC")
    assert t.column("ts_arrival").cast(pa.int64()).to_pylist()[0] == (T0 + 256_578) * 1000
    assert t.column("id").to_pylist() == ["6781", "6782"]
    assert t.column("price").type == pa.float64()
    meta = t.schema.metadata
    assert meta[b"quanta.source"] == b"tardis" and meta[b"quanta.source_sha256"]

    liq = pq.read_table(td.parquet_path(tmp_path, "binance-futures", "liquidations", D1, "BTCUSDT"))
    assert liq.column("id").to_pylist() == [None]  # empty id → null

    tick = pq.read_table(
        td.parquet_path(tmp_path, "binance-futures", "derivative_ticker", D1, "BTCUSDT")
    )
    assert "ts_funding" in tick.column_names and "funding_timestamp" not in tick.column_names
    assert tick.column("predicted_funding_rate").to_pylist() == [None]

    mirror = td.mirror_path(tmp_path, "binance-futures", "trades", D1, "BTCUSDT")
    side = json.loads(mirror.with_name(mirror.name + ".json").read_text())
    assert side["rows"] == 2 and side["bytes"] == mirror.stat().st_size
    assert side["md5"] == hashlib.md5(mirror.read_bytes()).hexdigest()
    assert side["first_arrival"].startswith("2025-10-01T00:00:00.256")

    # second run: served from the verified mirror, no new request
    hits = dict(fake.hits)
    rep2 = await td.run_import(tmp_path, jobs(list(td.SMALL_TYPES), [D1]), base_url=fake.url)
    assert rep2.counts() == {"cached": 3} and fake.hits == hits


async def test_cached_mirror_is_reconverted_when_parquet_missing(
    tmp_path: Path, fake: FakeTardis
) -> None:
    fake.add("binance-futures", "trades", "2025-10-01", "BTCUSDT", gz(TRADES))
    j = jobs(["trades"], [D1])
    await td.run_import(tmp_path, j, base_url=fake.url, convert=False)
    dest = td.parquet_path(tmp_path, "binance-futures", "trades", D1, "BTCUSDT")
    assert not dest.exists()
    rep = await td.run_import(tmp_path, j, base_url=fake.url)
    assert rep.counts() == {"cached": 1} and pq.read_table(dest).num_rows == 2
    assert sum(fake.hits.values()) == 1


async def test_paid_day_needs_key(tmp_path: Path, fake: FakeTardis) -> None:
    d2 = date(2025, 10, 2)
    fake.add("binance-futures", "trades", "2025-10-02", "BTCUSDT", gz(TRADES))
    rep = await td.run_import(tmp_path, jobs(["trades"], [d2]), base_url=fake.url)
    assert rep.counts() == {"needs_key": 1} and not fake.hits  # not even asked

    rep = await td.run_import(tmp_path, jobs(["trades"], [d2]), base_url=fake.url, api_key="wrong")
    assert rep.counts() == {"unauthorized": 1} and not rep.ok

    rep = await td.run_import(tmp_path, jobs(["trades"], [d2]), base_url=fake.url, api_key=KEY)
    assert rep.counts() == {"downloaded": 1}
    assert fake.auth_seen[-1] == f"Bearer {KEY}"


async def test_missing_is_not_an_error(tmp_path: Path, fake: FakeTardis) -> None:
    rep = await td.run_import(tmp_path, jobs(["liquidations"], [D1]), base_url=fake.url)
    assert rep.counts() == {"missing": 1} and rep.ok


async def test_corrupt_files_are_never_kept(tmp_path: Path, fake: FakeTardis) -> None:
    full = gz(TRADES * 200)
    fake.add("binance-futures", "trades", "2025-10-01", "BTCUSDT", full[: len(full) // 2])
    rel = fake.add("binance-futures", "liquidations", "2025-10-01", "BTCUSDT", gz(LIQUIDATIONS))
    fake.bad_md5.add(rel)
    fake.add("binance-futures", "derivative_ticker", "2025-10-01", "BTCUSDT", b"not gzip at all")
    rep = await td.run_import(tmp_path, jobs(list(td.SMALL_TYPES), [D1]), base_url=fake.url)
    assert rep.counts() == {"corrupt": 3} and not rep.ok
    by_type = {r.data_type: r.error or "" for r in rep.results}
    assert "md5" in by_type["liquidations"]
    assert "EOFError" in by_type["trades"] or "BadGzip" in by_type["trades"]
    assert fake.hits[rel] == 3  # retried
    assert not list((tmp_path / "archive").rglob("*.csv.gz*"))
    assert not (tmp_path / "lake").exists() or not list((tmp_path / "lake").rglob("*.parquet*"))


async def test_quota_stops_the_run_and_resume_continues(tmp_path: Path, fake: FakeTardis) -> None:
    days = td.month_starts("2025-07", "2025-10")
    for d in days:
        fake.add("binance-futures", "trades", d.isoformat(), "BTCUSDT", gz(TRADES))
    fake.quota_after = 2
    j = jobs(["trades"], days)
    rep = await td.run_import(tmp_path, j, base_url=fake.url, concurrency=1)
    assert rep.counts() == {"downloaded": 2, "quota": 2}
    assert rep.quota_retry_after_s == 16912 and rep.ok
    out = td.summary(rep)
    assert out["quota"]["retry_after_s"] == 16912

    fake.quota_after = None
    rep = await td.run_import(tmp_path, j, base_url=fake.url, concurrency=1)
    assert rep.counts() == {"cached": 2, "downloaded": 2}


async def test_short_rate_limit_is_waited_out(tmp_path: Path, fake: FakeTardis) -> None:
    rel = fake.add("binance-futures", "trades", "2025-10-01", "BTCUSDT", gz(TRADES))
    fake.limit_once, fake.retry_after = True, "0"
    rep = await td.run_import(tmp_path, jobs(["trades"], [D1]), base_url=fake.url)
    assert rep.counts() == {"downloaded": 1} and rep.quota_retry_after_s is None
    assert fake.hits[rel] == 2


async def test_l2_only_for_selected_months(tmp_path: Path, fake: FakeTardis) -> None:
    for d in ("2025-09-01", "2025-10-01"):
        fake.add("binance-futures", "trades", d, "BTCUSDT", gz(TRADES))
    fake.add("binance-futures", "incremental_book_L2", "2025-10-01", "BTCUSDT", gz(L2))
    fake.add("binance-futures", "book_snapshot_25", "2025-10-01", "BTCUSDT", gz(SNAP25))
    days = td.month_starts("2025-09", "2025-10")
    j = jobs(["trades"], days, (D1,))
    assert [(x.data_type, x.day.isoformat()) for x in j] == [
        ("trades", "2025-09-01"),
        ("trades", "2025-10-01"),
        ("book_snapshot_25", "2025-10-01"),
        ("incremental_book_L2", "2025-10-01"),
    ]
    rep = await td.run_import(tmp_path, j, base_url=fake.url)
    assert rep.counts() == {"downloaded": 4}
    l2 = pq.read_table(
        td.parquet_path(tmp_path, "binance-futures", "incremental_book_L2", D1, "BTCUSDT")
    )
    assert l2.column("is_snapshot").to_pylist() == [True, False]
    snap = pq.read_table(
        td.parquet_path(tmp_path, "binance-futures", "book_snapshot_25", D1, "BTCUSDT")
    )
    assert snap.column("bids[1].amount").to_pylist() == [2.0]
    assert snap.schema.field("asks[0].price").type == pa.float64()


async def test_empty_gzip_means_no_data(tmp_path: Path, fake: FakeTardis) -> None:
    rel = fake.add("deribit", "liquidations", "2025-10-01", "BTC-PERPETUAL", gzip.compress(b""))
    j = td.plan_jobs(["deribit"], {"deribit": ["BTC-PERPETUAL"]}, [D1], ["liquidations"])
    rep = await td.run_import(tmp_path, j, base_url=fake.url)
    assert rep.counts() == {"empty": 1} and rep.ok
    assert not td.parquet_path(tmp_path, "deribit", "liquidations", D1, "BTC-PERPETUAL").exists()
    rep = await td.run_import(tmp_path, j, base_url=fake.url)
    assert rep.counts() == {"cached": 1} and fake.hits[rel] == 1


def test_header_only_file_gives_empty_table(tmp_path: Path) -> None:
    src = tmp_path / "x.csv.gz"
    src.write_bytes(gz(TRADES[:1]))
    dest = tmp_path / "x.parquet"
    conv = td.convert_gz(src, dest, {"source": "tardis"})
    assert conv.rows == 0 and pq.read_table(dest).num_rows == 0


def test_symbols_and_months() -> None:
    ex = ["binance-futures", "deribit"]
    assert td.parse_symbols(["btcusdt", "deribit:BTC-PERPETUAL"], ex) == {
        "binance-futures": ["BTCUSDT"],
        "deribit": ["BTCUSDT", "BTC-PERPETUAL"],
    }
    with pytest.raises(ValueError, match="okex-swap"):
        td.parse_symbols(["okex-swap:BTC-USDT-SWAP"], ex)
    assert td.month_starts("2024-11", "2025-02") == [
        date(2024, 11, 1),
        date(2024, 12, 1),
        date(2025, 1, 1),
        date(2025, 2, 1),
    ]
    assert td.dataset_url(
        "https://x/v1", "deribit", "trades", date(2020, 3, 1), "BTC-PERPETUAL"
    ) == ("https://x/v1/deribit/trades/2020/03/01/BTC-PERPETUAL.csv.gz")
