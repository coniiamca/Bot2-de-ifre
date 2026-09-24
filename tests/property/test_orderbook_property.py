from hypothesis import given, settings
from hypothesis import strategies as st

from quanta.core.ticks import Scale
from quanta.marketstate.orderbook import L2Book

level = st.tuples(st.integers(1, 200), st.integers(0, 5))


@settings(max_examples=300, deadline=None)
@given(st.lists(st.tuples(st.lists(level, max_size=5), st.lists(level, max_size=5)), max_size=40))
def test_book_matches_naive_model(
    batches: list[tuple[list[tuple[int, int]], list[tuple[int, int]]]],
) -> None:
    ps, qs = Scale("0.1"), Scale("1")
    book = L2Book("X", ps, qs)
    bids: dict[int, int] = {}
    asks: dict[int, int] = {}
    for b, a in batches:
        book.apply([(ps.to_str(p), str(q)) for p, q in b], [(ps.to_str(p), str(q)) for p, q in a])
        for model, updates in ((bids, b), (asks, a)):
            for p, q in updates:
                if q == 0:
                    model.pop(p, None)
                else:
                    model[p] = q
        assert book.bids == bids and book.asks == asks
        assert book.best_bid == (max(bids) if bids else None)
        assert book.best_ask == (min(asks) if asks else None)
