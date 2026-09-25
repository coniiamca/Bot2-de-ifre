"""Offline order book audit: replay recorded depth diffs and compare against recorded REST
snapshots (resync + periodic audit snapshots).

For a snapshot with ``lastUpdateId = L`` the book is compared right after applying the diff
event with ``U <= L <= u``; price levels touched by that event are excluded (they may have
changed between L and u). Only the price range covered by the snapshot is compared (a
1000-level snapshot does not show deeper levels). Any mismatch means our reconstruction —
sequencing, parsing or scaling — is wrong.

Diff events are streamed (a day at 100 ms is ~864k events per symbol): a small heap restores
update-id order across overlapping connections (make-before-break rotation) and drops the
duplicates, so memory stays bounded.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import msgspec

from quanta.core.ticks import Scale, ScaleError
from quanta.marketstate.orderbook import L2Book
from quanta.tools.raw_reader import days_around, decode_rest, iter_records, segment_files
from quanta.venues.binance_usdm import messages as msg

VENUE = "binance_usdm"
REORDER_WINDOW = 5000  # events (~8 min of one symbol at 100 ms)


@dataclass(frozen=True, slots=True)
class Snapshot:
    last_update_id: int
    recv_ns: int
    purpose: str
    bids: list[list[str]]
    asks: list[list[str]]


@dataclass(slots=True)
class AuditReport:
    symbol: str
    day: str
    events: int = 0
    snapshots: int = 0
    seeded: int = 0
    compared: int = 0
    matched: int = 0
    mismatched: int = 0
    chain_breaks: int = 0
    errors: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    mismatch_examples: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.compared > 0 and self.mismatched == 0 and self.errors == 0

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def _raw_events(data_dir: Path, symbol: str, day: date) -> Iterator[msg.DepthUpdate]:
    prefix = f"{symbol.lower()}@depth"
    for rec in iter_records(segment_files(data_dir, VENUE, "public_depth", days_around(day, 0, 0))):
        if rec.k != "ws":
            continue
        env = msg.decode_envelope(bytes(rec.p))
        if env.stream.startswith(prefix):
            yield msg.decode_depth(env.data)


def in_order(
    events: Iterable[msg.DepthUpdate], window: int = REORDER_WINDOW
) -> Iterator[msg.DepthUpdate]:
    """Arrival order → update-id order, without duplicates (bounded reorder buffer)."""
    heap: list[tuple[int, int, msg.DepthUpdate]] = []
    last: int | None = None
    for n, ev in enumerate(events):
        heapq.heappush(heap, (ev.u, n, ev))
        if len(heap) > window:
            u, _, out = heapq.heappop(heap)
            if last is None or u > last:
                last = u
                yield out
    while heap:
        u, _, out = heapq.heappop(heap)
        if last is None or u > last:
            last = u
            yield out


def _load(
    data_dir: Path, symbol: str, day: date
) -> tuple[list[Snapshot], tuple[Scale, Scale] | None]:
    days = days_around(day, 0, 0)
    snaps: list[Snapshot] = []
    scales: tuple[Scale, Scale] | None = None
    for rec in iter_records(segment_files(data_dir, VENUE, "rest", days)):
        if rec.k != "rest":
            continue
        rp = decode_rest(rec.p)
        if rp.status != 200:
            continue
        if rp.path == "/fapi/v1/depth" and rp.params.get("symbol") == symbol:
            body = msgspec.json.decode(bytes(rp.body))
            snaps.append(
                Snapshot(
                    int(body["lastUpdateId"]),
                    rec.t,
                    rp.purpose,
                    body.get("bids", []),
                    body.get("asks", []),
                )
            )
        elif rp.path == "/fapi/v1/exchangeInfo" and scales is None:
            info = msgspec.json.decode(bytes(rp.body))
            for s in info.get("symbols", []):
                if s.get("symbol") == symbol:
                    f = {x["filterType"]: x for x in s["filters"]}
                    scales = (
                        Scale(f["PRICE_FILTER"]["tickSize"]),
                        Scale(f["LOT_SIZE"]["stepSize"]),
                    )
    snaps.sort(key=lambda s: s.last_update_id)
    return snaps, scales


def _infer_scale(values: list[str]) -> Scale:
    # exchange strings are formatted at instrument precision ("99.0" → 1 decimal)
    decimals = max((len(v.partition(".")[2]) for v in values), default=0)
    return Scale("1" if decimals == 0 else "0." + "0" * (decimals - 1) + "1")


def _compare(book: L2Book, snap: Snapshot, exclude: set[int], rep: AuditReport) -> bool:
    ps, qs = book.price_scale, book.qty_scale
    ok = True
    diffs: list[str] = []
    for side_name, ours, levels, is_bid in (
        ("bid", book.bids, snap.bids, True),
        ("ask", book.asks, snap.asks, False),
    ):
        theirs = {ps.to_int(p): qs.to_int(q) for p, q in levels}
        if not theirs:
            continue
        bound = min(theirs) if is_bid else max(theirs)
        in_range = {p: q for p, q in ours.items() if (p >= bound if is_bid else p <= bound)}
        for price in set(theirs) | set(in_range):
            if price in exclude:
                continue
            if theirs.get(price) != in_range.get(price):
                ok = False
                if len(diffs) < 5:
                    diffs.append(
                        f"{side_name} {ps.to_str(price)}: snapshot={theirs.get(price)}"
                        f" book={in_range.get(price)}"
                    )
    if not ok and len(rep.mismatch_examples) < 10:
        rep.mismatch_examples.append({"last_update_id": snap.last_update_id, "diffs": diffs})
    return ok


def audit(data_dir: Path, symbol: str, day: date) -> AuditReport:
    snaps, scales = _load(data_dir, symbol, day)
    rep = AuditReport(symbol, day.isoformat(), snapshots=len(snaps))
    events = in_order(_raw_events(data_dir, symbol, day))
    if not snaps:
        rep.events = sum(1 for _ in events)
        return rep
    if scales is None:
        prices = [p for s in snaps for p, _ in s.bids + s.asks]
        qtys = [q for s in snaps for _, q in s.bids + s.asks]
        scales = (_infer_scale(prices), _infer_scale(qtys))

    def check(snap: Snapshot, bk: L2Book, exclude: set[int]) -> bool:
        rep.compared += 1
        if _compare(bk, snap, exclude, rep):
            rep.matched += 1
            return True
        rep.mismatched += 1
        return False

    book: L2Book | None = None
    last_u: int | None = None
    j = 0
    for ev in events:
        rep.events += 1
        try:
            book, last_u, j = _step(ev, book, last_u, j, snaps, scales, symbol, rep, check)
        except ScaleError as exc:
            # A level that cannot be represented at instrument precision: corrupt data or an
            # unnoticed instrument change. Either way the reconstruction is not trustworthy.
            rep.errors += 1
            if len(rep.mismatch_examples) < 10:
                rep.mismatch_examples.append({"u": ev.u, "error": str(exc)})
            book, last_u = None, None
    for _ in snaps[j:]:
        rep.skip("after_last_event")
    return rep


def _step(
    ev: msg.DepthUpdate,
    book: L2Book | None,
    last_u: int | None,
    j: int,
    snaps: list[Snapshot],
    scales: tuple[Scale, Scale],
    symbol: str,
    rep: AuditReport,
    check: Any,
) -> tuple[L2Book | None, int | None, int]:
    """Process one diff event; returns the updated (book, last_u, snapshot index)."""
    if book is not None and ev.pu != last_u:
        rep.chain_breaks += 1
        book, last_u = None, None

    if book is None:
        # seed from a snapshot this event bridges: U <= L <= u, or pu == L (successor)
        while (
            j < len(snaps) and snaps[j].last_update_id < ev.U and snaps[j].last_update_id != ev.pu
        ):
            rep.skip("no_bridging_event")
            j += 1
        if j < len(snaps) and snaps[j].last_update_id <= ev.u:
            book = L2Book(symbol, *scales)
            book.load_snapshot(snaps[j].bids, snaps[j].asks)
            book.apply(ev.b, ev.a)
            last_u = ev.u
            rep.seeded += 1
            j += 1
            touched = {scales[0].to_int(p) for p, _ in ev.b + ev.a}
            while book is not None and j < len(snaps) and snaps[j].last_update_id <= ev.u:
                if not check(snaps[j], book, touched):
                    book = None
                j += 1
        return book, last_u, j

    # Snapshots strictly between the previous event and this one see the current state.
    while j < len(snaps) and snaps[j].last_update_id < ev.U:
        if last_u is not None and snaps[j].last_update_id >= last_u:
            ok = check(snaps[j], book, set())
        else:
            rep.skip("behind_chain")
            ok = True
        j += 1
        if not ok:
            return None, None, j

    book.apply(ev.b, ev.a)
    touched = {scales[0].to_int(p) for p, _ in ev.b + ev.a}
    while j < len(snaps) and snaps[j].last_update_id <= ev.u:
        ok = check(snaps[j], book, touched)
        j += 1
        if not ok:
            return None, None, j
    return book, ev.u, j
