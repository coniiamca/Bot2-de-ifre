"""Every protection the user asked for has a test that shows it refusing: kill switch,
position limits, daily loss, stale data, duplicate orders, unexpected price, disconnects,
reduce-only exits."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from quanta.core.clock import SimClock
from quanta.oms.oms import Intent
from quanta.oms.state import OrderStatus
from quanta.oms.store import Store, WriterLockError
from quanta.risk.killswitch import KillSwitch, Level
from quanta.risk.limits import Context, DailyLoss, MarketView, RiskLimits, check

LIM = RiskLimits()
MKT = MarketView(bid=64999.9, ask=65000.1, mark=65000.0, book_age_s=0.2, mark_age_s=0.5)
BUY = Intent("entry", "BTCUSDT", "BUY", "LIMIT", Decimal("0.002"), Decimal("64999.9"), "GTX")


def ctx(**kw: object) -> Context:
    base = Context(level=Level.ACTIVE, equity=10_000.0, positions={}, marks={"BTCUSDT": 65000.0})
    return replace(base, **kw)  # type: ignore[arg-type]


def test_a_normal_entry_passes() -> None:
    assert check(BUY, ctx(), MKT, LIM) is None


def test_kill_switch_blocks_entries_but_not_exits() -> None:
    for level in (Level.PAUSED, Level.REDUCING, Level.HALTED, Level.FLATTENING):
        assert "kill switch" in (check(BUY, ctx(level=level), MKT, LIM) or "")
    exit_ = Intent(
        "exit",
        "BTCUSDT",
        "SELL",
        "LIMIT",
        Decimal("0.002"),
        Decimal("64990"),
        "IOC",
        reduce_only=True,
    )
    held = ctx(level=Level.HALTED, positions={"BTCUSDT": Decimal("0.002")})
    assert check(exit_, held, MKT, LIM) is None


def test_position_limits() -> None:
    big = replace(BUY, qty=Decimal("0.02"))  # 1300 USDT > 1000 per order
    assert "emir büyüklüğü" in (check(big, ctx(), MKT, LIM) or "")
    small_equity = ctx(equity=100.0)
    assert "coin başına" in (check(BUY, small_equity, MKT, LIM) or "")
    three = {"ETHUSDT": Decimal(1), "SOLUSDT": Decimal(1), "XRPUSDT": Decimal(1)}
    many = ctx(
        positions=three, marks={"BTCUSDT": 65000.0, "ETHUSDT": 1.0, "SOLUSDT": 1.0, "XRPUSDT": 1.0}
    )
    assert "en fazla 3" in (check(BUY, many, MKT, LIM) or "")
    gross = ctx(positions={"ETHUSDT": Decimal(5)}, marks={"BTCUSDT": 65000.0, "ETHUSDT": 2990.0})
    assert "toplam kaldıraç" in (check(BUY, gross, MKT, LIM) or "")
    assert "saatte" in (check(BUY, ctx(new_positions_last_hour=6), MKT, LIM) or "")


def test_stale_data_disconnect_and_clock() -> None:
    assert "bayat" in (check(BUY, ctx(), replace(MKT, book_age_s=3.0), LIM) or "")
    assert "bayat" in (check(BUY, ctx(), replace(MKT, mark_age_s=6.0), LIM) or "")
    assert "akışı" in (check(BUY, ctx(stream_down_s=11.0), MKT, LIM) or "")
    assert "saat farkı" in (check(BUY, ctx(clock_offset_s=0.3), MKT, LIM) or "")
    assert "donduruldu" in (check(BUY, ctx(frozen={"BTCUSDT"}), MKT, LIM) or "")


def test_unexpected_price_and_spread() -> None:
    far = replace(BUY, price=Decimal("63000"))  # 3 % below the mark
    assert "beklenmeyen fiyat" in (check(far, ctx(), MKT, LIM) or "")
    wide = replace(MKT, bid=64800.0, ask=65200.0)
    market = Intent("entry", "BTCUSDT", "BUY", "MARKET", Decimal("0.002"))
    assert "spread" in (check(market, ctx(), wide, LIM) or "")
    off = replace(MKT, bid=66299.0, ask=66301.0)  # book 2 % above the mark
    assert "mark'tan çok uzak" in (check(market, ctx(), off, LIM) or "")


def test_order_rate_and_reduce_only_sanity() -> None:
    assert "emir hızı" in (check(BUY, ctx(orders_last_min=30), MKT, LIM) or "")
    flatten = Intent("flatten", "BTCUSDT", "SELL", "MARKET", Decimal("0.002"), reduce_only=True)
    busy = ctx(orders_last_min=99, positions={"BTCUSDT": Decimal("0.002")})
    assert check(flatten, busy, MKT, LIM) is None  # flattening is never rate-limited
    wrong_way = Intent("exit", "BTCUSDT", "BUY", "MARKET", Decimal("0.002"), reduce_only=True)
    held = ctx(positions={"BTCUSDT": Decimal("0.002")})
    assert "azaltılacak" in (check(wrong_way, held, MKT, LIM) or "")


def test_daily_loss_levels_and_new_day(tmp_path: Path) -> None:
    clock = SimClock(1_790_000_000 * 10**9)
    store = Store(tmp_path / "t.db")
    dl = DailyLoss(store, LIM)
    assert dl.update(clock.now_ns(), 10_000.0) == (None, "")
    assert dl.update(clock.now_ns(), 9_790.0)[0] is Level.REDUCING  # −2.1 %
    assert dl.update(clock.now_ns(), 9_690.0)[0] is Level.FLATTENING  # −3.1 %
    clock.advance_to(clock.now_ns() + 86_400 * 10**9)  # a new UTC day: start from here
    assert dl.update(clock.now_ns(), 9_690.0) == (None, "")
    assert dl.update(clock.now_ns(), 8_990.0)[0] is Level.HALTED  # 10.1 % below the peak
    store.close()
    again = DailyLoss(Store(tmp_path / "t.db"), LIM)  # persisted
    assert again.peak == 10_000.0


def test_kill_switch_persists_and_only_resume_leaves_halt(tmp_path: Path) -> None:
    clock = SimClock(1)
    store = Store(tmp_path / "k.db")
    ks = KillSwitch(store, clock)
    ks.condition("stale_data", Level.PAUSED, "book 3 s")
    assert ks.level is Level.PAUSED and not ks.allows_entry()
    ks.condition("stale_data", None)
    assert ks.level is Level.ACTIVE
    ks.escalate(Level.HALTED, "günlük zarar")
    ks.escalate(Level.PAUSED, "lower: ignored")
    assert ks.level is Level.HALTED
    store.close()
    store2 = Store(tmp_path / "k.db")
    ks2 = KillSwitch(store2, clock)
    assert ks2.level is Level.HALTED and ks2.reasons == ["günlük zarar"]  # survives a restart
    ks2.resume(by="human")
    assert ks2.level is Level.ACTIVE
    kinds = [j["kind"] for j in store2.recent_journal()]
    assert kinds.count("kill_level") == 2


def test_single_writer_and_epochs(tmp_path: Path) -> None:
    s1 = Store(tmp_path / "w.db")
    with pytest.raises(WriterLockError):
        Store(tmp_path / "w.db")
    assert s1.epoch == 1
    s1.add_order(1, "q1-1-1", 1, "BTCUSDT", "BUY", "LIMIT", "entry", False, "0.002", "65000")
    s1.update_order(2, "q1-1-1", OrderStatus.SENT, sent=True)
    s1.close()
    s2 = Store(tmp_path / "w.db")
    assert s2.epoch == 2 and s2.order("q1-1-1").status is OrderStatus.SENT  # type: ignore[union-attr]
    assert s2.add_fill(
        "BTCUSDT",
        7,
        "q1-1-1",
        "BUY",
        Decimal("0.002"),
        Decimal(65000),
        Decimal("0.03"),
        Decimal(0),
        1,
    )
    assert not s2.add_fill(
        "BTCUSDT",
        7,
        "q1-1-1",
        "BUY",
        Decimal("0.002"),
        Decimal(65000),
        Decimal("0.03"),
        Decimal(0),
        1,
    )
    assert s2.positions() == {"BTCUSDT": Decimal("0.002")}  # a duplicate event counts once
