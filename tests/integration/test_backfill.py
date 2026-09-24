"""Backfill against a local fake archive (checksums, missing files, formats)."""

from __future__ import annotations

import hashlib
import io
import zipfile
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from aiohttp import web

from quanta.archive.binance_vision import archive_path, backfill


def _zip(name: str, lines: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, "\n".join(lines) + "\n")
    return buf.getvalue()


class FakeArchive:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.bad_checksum: set[str] = set()
        self.hits: dict[str, int] = {}
        self.port = 0

    def add(self, rel: str, data: bytes, corrupt: bool = False) -> None:
        self.files[rel] = data
        if corrupt:
            self.bad_checksum.add(rel)

    async def handler(self, request: web.Request) -> web.Response:
        path = request.path.lstrip("/")
        self.hits[path] = self.hits.get(path, 0) + 1
        if path.endswith(".CHECKSUM"):
            rel = path[: -len(".CHECKSUM")]
            if rel not in self.files:
                raise web.HTTPNotFound()
            digest = hashlib.sha256(self.files[rel]).hexdigest()
            if rel in self.bad_checksum:
                digest = "0" * 64
            return web.Response(text=f"{digest}  {rel.rsplit('/', 1)[-1]}\n")
        if path not in self.files:
            raise web.HTTPNotFound()
        return web.Response(body=self.files[path])

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@pytest.fixture
async def archive() -> AsyncIterator[FakeArchive]:
    fa = FakeArchive()
    app = web.Application()
    app.router.add_get("/{tail:.*}", fa.handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    fa.port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    yield fa
    await runner.cleanup()


AGG_HEADER = "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker"


async def test_backfill_verifies_converts_and_reports(tmp_path: Path, archive: FakeArchive) -> None:
    d1, d2, _d3, d4 = (date(2026, 9, i) for i in (1, 2, 3, 4))
    # day 1: header + ms timestamps; day 2: no header + µs timestamps; day 3 missing;
    # day 4: corrupted (checksum mismatch)
    archive.add(
        archive_path("aggTrades", "BTCUSDT", "daily", d1.isoformat()),
        _zip("a.csv", [AGG_HEADER, "1,100.5,0.010,10,11,1788220800000,true"]),
    )
    archive.add(
        archive_path("aggTrades", "BTCUSDT", "daily", d2.isoformat()),
        _zip("b.csv", ["2,100.6,0.020,12,12,1788307200000123,false"]),
    )
    archive.add(
        archive_path("aggTrades", "BTCUSDT", "daily", d4.isoformat()),
        _zip("d.csv", ["3,1,1,1,1,1788480000000,true"]),
        corrupt=True,
    )
    rep = await backfill(tmp_path, "aggTrades", "BTCUSDT", d1, d4, base_url=archive.url)
    statuses = [r.status for r in rep.results]
    assert statuses == ["downloaded", "downloaded", "missing", "checksum_mismatch"]
    assert not rep.ok  # a checksum mismatch is a failure
    corrupt = (
        tmp_path
        / "archive"
        / "binance_vision"
        / archive_path("aggTrades", "BTCUSDT", "daily", d4.isoformat())
    )
    assert not corrupt.exists(), "a file failing its checksum is never stored"
    assert rep.converted_rows == {d1.isoformat(): 1, d2.isoformat(): 1}
    t2 = pq.read_table(
        tmp_path
        / "lake"
        / "binance_vision_um"
        / "aggTrades"
        / "symbol=BTCUSDT"
        / f"date={d2.isoformat()}"
        / "part-0.parquet"
    )
    assert t2.schema.field("transact_time").type == pa.timestamp("ns", tz="UTC")
    # µs input converted exactly: 1788307200000123 µs → ns
    assert t2.column("transact_time").cast(pa.int64()).to_pylist() == [1788307200000123000]
    assert t2.column("is_buyer_maker").to_pylist() == [False]
    # second run: served from the verified mirror, no re-download
    hits = dict(archive.hits)
    rep2 = await backfill(tmp_path, "aggTrades", "BTCUSDT", d1, d2, base_url=archive.url)
    assert [r.status for r in rep2.results] == ["cached", "cached"]
    assert archive.hits == hits


async def test_monthly_funding_and_datetime_columns(tmp_path: Path, archive: FakeArchive) -> None:
    archive.add(
        archive_path("fundingRate", "ETHUSDT", "monthly", "2026-08"),
        _zip(
            "f.csv",
            ["calc_time,funding_interval_hours,last_funding_rate", "1785542400000,8,0.0001"],
        ),
    )
    archive.add(
        archive_path("metrics", "ETHUSDT", "daily", "2026-08-01"),
        _zip(
            "m.csv",
            [
                "create_time,symbol,sum_open_interest,sum_open_interest_value,"
                "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
                "count_long_short_ratio,sum_taker_long_short_vol_ratio",
                "2026-08-01 00:05:00,ETHUSDT,1,2,3,4,5,6",
            ],
        ),
    )
    rep = await backfill(
        tmp_path,
        "fundingRate",
        "ETHUSDT",
        date(2026, 8, 3),
        date(2026, 8, 20),
        base_url=archive.url,
    )
    assert rep.converted_rows == {"2026-08": 1}
    rep = await backfill(
        tmp_path, "metrics", "ETHUSDT", date(2026, 8, 1), date(2026, 8, 1), base_url=archive.url
    )
    t = pq.read_table(
        tmp_path
        / "lake"
        / "binance_vision_um"
        / "metrics"
        / "symbol=ETHUSDT"
        / "date=2026-08-01"
        / "part-0.parquet"
    )
    assert t.column("create_time").cast(pa.int64()).to_pylist() == [1785542700 * 10**9]
    assert t.column("sum_taker_long_short_vol_ratio").to_pylist() == [6.0]


async def test_unknown_dataset_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        await backfill(tmp_path, "nope", "X", date(2026, 1, 1), date(2026, 1, 1))
