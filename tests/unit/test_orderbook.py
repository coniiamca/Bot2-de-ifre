from quanta.core.ticks import Scale
from quanta.marketstate.orderbook import L2Book


def make() -> L2Book:
    return L2Book("X", Scale("0.1"), Scale("0.001"))


def test_snapshot_apply_delete_and_best() -> None:
    b = make()
    b.load_snapshot([["100.0", "1.000"], ["99.9", "2.000"]], [["100.1", "1.500"]])
    assert b.best_bid == 1000 and b.best_ask == 1001 and b.spread_ticks() == 1
    b.apply([["100.0", "0.000"]], [["100.2", "3.000"]])
    assert b.best_bid == 999
    b.apply([], [["100.1", "0"]])
    assert b.best_ask == 1002
    b.apply([["100.0", "0.000"]], [])  # deleting an absent level is a no-op
    assert b.depth() == (1, 1)


def test_crossed_detection() -> None:
    b = make()
    b.load_snapshot([["100.0", "1"]], [["100.1", "1"]])
    assert not b.is_crossed
    b.apply([["100.2", "1"]], [])
    assert b.is_crossed
