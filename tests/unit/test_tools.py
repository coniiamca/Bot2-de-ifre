"""The verification tools must detect real problems (not only pass on clean data)."""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from quanta.core.clock import NS_PER_S, SimClock
from quanta.recorder.records import rest_record, ws_record
from quanta.recorder.segment import SegmentWriter
from quanta.tools.book_audit import audit
from quanta.tools.verify_aggtrades import TradeRow, compare, read_archive, read_recorded
from quanta.tools.volume import volume

DAY = date(2026, 9, 24)
T0 = int(datetime(2026, 9, 24, 10, tzinfo=UTC).timestamp()) * NS_PER_S


def _depth_msg(U: int, u: int, pu: int, bids: list[list[str]], asks: list[list[str]]) -> bytes:
    data = {
        "e": "depthUpdate",
        "E": 1,
        "T": 1,
        "s": "BTCUSDT",
        "U": U,
        "u": u,
        "pu": pu,
        "b": bids,
        "a": asks,
    }
    return json.dumps({"stream": "btcusdt@depth@100ms", "data": data}).encode()


def _write_depth_dataset(root: Path, tamper: bool) -> None:
    """Snapshot(100) → events 101..110 → audit snapshot at 110 → events → snapshot 120."""
    clock = SimClock(T0)
    depth = SegmentWriter(root, "binance_usdm", "public_depth", clock, fsync=False)
    rest = SegmentWriter(root, "binance_usdm", "rest", clock, fsync=False)
    book = {
        "bids": {"99.0": "1.000", "98.0": "2.000"},
        "asks": {"101.0": "1.000", "102.0": "3.000"},
    }

    def snap(last_id: int, t: int) -> None:
        body = json.dumps(
            {
                "lastUpdateId": last_id,
                "bids": sorted(book["bids"].items(), reverse=True),
                "asks": sorted(book["asks"].items()),
            }
        ).encode()
        rest.append(
            t,
            rest_record(
                t,
                path="/fapi/v1/depth",
                params={"symbol": "BTCUSDT"},
                status=200,
                sent_ns=t,
                body=body,
                purpose="depth_audit",
            ),
        )

    t = T0
    snap(100, t)
    last_u = 100
    for i in range(1, 21):
        t += 1_000_000
        price = f"{99 - (i % 3)}.0"
        qty = f"{i}.000"
        book["bids"][price] = qty
        sent = [[price, qty]]
        if tamper and i == 5:
            sent.append(["98.5", "7.000"])  # phantom level the exchange never had
        depth.append(
            t, ws_record(t, "c#1", i, _depth_msg(last_u + 1, last_u + 1, last_u, sent, []))
        )
        last_u += 1
        if last_u in (110, 120):
            snap(last_u, t)

    async def close() -> None:
        await depth.close()
        await rest.close()

    asyncio.run(close())


def test_book_audit_passes_on_consistent_data(tmp_path: Path) -> None:
    _write_depth_dataset(tmp_path, tamper=False)
    rep = audit(tmp_path, "BTCUSDT", DAY)
    assert rep.ok and rep.compared == 2 and rep.seeded == 1


def test_book_audit_detects_corrupted_reconstruction(tmp_path: Path) -> None:
    _write_depth_dataset(tmp_path, tamper=True)
    rep = audit(tmp_path, "BTCUSDT", DAY)
    assert not rep.ok and rep.mismatched >= 1
    assert rep.mismatch_examples


def _zip_csv(rows: list[str], header: bool) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        lines = (
            [
                "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,"
                "is_buyer_maker"
            ]
            if header
            else []
        ) + rows
        zf.writestr("X-aggTrades.csv", "\n".join(lines) + "\n")
    return buf.getvalue()


def test_read_archive_with_and_without_header(tmp_path: Path) -> None:
    rows = ["1,100.5,0.010,10,11,1790000000000,true", "2,100.6,0.020,12,12,1790000000001,false"]
    for header in (True, False):
        p = tmp_path / f"a{header}.zip"
        p.write_bytes(_zip_csv(rows, header))
        got = read_archive(p)
        assert got[1] == TradeRow(
            1, Decimal("100.5"), Decimal("0.010"), 10, 11, 1790000000000, True
        )
        assert len(got) == 2


def test_compare_reports_missing_extra_and_mismatch() -> None:
    def row(i: int, qty: str = "1") -> TradeRow:
        return TradeRow(i, Decimal("1"), Decimal(qty), i, i, i, False)

    archive = {i: row(i) for i in range(1, 11)}
    recorded = {i: row(i) for i in (2, 3, 4, 7, 8, 9)}
    recorded[8] = row(8, "2")
    recorded[99] = row(99)
    rep = compare("X", DAY, archive, recorded)
    assert rep.missing_ranges == [(5, 6), (10, 10)]  # within window [2, 99]
    assert rep.extra_ids == 1 and rep.mismatched == 1 and not rep.ok


def test_read_recorded_filters_symbol_and_day(tmp_path: Path) -> None:
    clock = SimClock(T0)
    w = SegmentWriter(tmp_path, "binance_usdm", "market", clock, fsync=False)
    ms = T0 // 1_000_000
    for sym, a, ts in (("BTCUSDT", 1, ms), ("ETHUSDT", 2, ms), ("BTCUSDT", 3, ms + 86_400_000)):
        data = {
            "e": "aggTrade",
            "E": ts,
            "s": sym,
            "a": a,
            "p": "1",
            "q": "1",
            "f": 1,
            "l": 1,
            "T": ts,
            "m": True,
        }
        w.append(
            T0,
            ws_record(
                T0, "c", a, json.dumps({"stream": f"{sym.lower()}@aggTrade", "data": data}).encode()
            ),
        )
    asyncio.run(w.close())
    assert set(read_recorded(tmp_path, "BTCUSDT", DAY)) == {1}
    vol = volume(tmp_path)
    assert vol[DAY.isoformat()]["binance_usdm/market"]["records"] == 3
