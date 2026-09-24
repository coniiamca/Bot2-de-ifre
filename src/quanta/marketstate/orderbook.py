"""L2 (market-by-price) order book with integer prices/quantities.

Applies absolute-quantity level updates (quantity 0 deletes the level; deleting an absent
level is a no-op, as documented by Binance). Best bid/ask are cached and recomputed lazily.
"""

from __future__ import annotations

from collections.abc import Iterable

from quanta.core.ticks import Scale


class L2Book:
    __slots__ = (
        "_ask_dirty",
        "_best_ask",
        "_best_bid",
        "_bid_dirty",
        "asks",
        "bids",
        "price_scale",
        "qty_scale",
        "symbol",
    )

    def __init__(self, symbol: str, price_scale: Scale, qty_scale: Scale) -> None:
        self.symbol = symbol
        self.price_scale = price_scale
        self.qty_scale = qty_scale
        self.bids: dict[int, int] = {}
        self.asks: dict[int, int] = {}
        self._best_bid: int | None = None
        self._best_ask: int | None = None
        self._bid_dirty = False
        self._ask_dirty = False

    def clear(self) -> None:
        self.bids.clear()
        self.asks.clear()
        self._best_bid = self._best_ask = None
        self._bid_dirty = self._ask_dirty = False

    def load_snapshot(
        self,
        bids: Iterable[tuple[str, str] | list[str]],
        asks: Iterable[tuple[str, str] | list[str]],
    ) -> None:
        self.clear()
        self.apply(bids, asks)

    def apply(
        self,
        bids: Iterable[tuple[str, str] | list[str]],
        asks: Iterable[tuple[str, str] | list[str]],
    ) -> None:
        ps, qs = self.price_scale, self.qty_scale
        for p, q in bids:
            self._set(self.bids, ps.to_int(p), qs.to_int(q), is_bid=True)
        for p, q in asks:
            self._set(self.asks, ps.to_int(p), qs.to_int(q), is_bid=False)

    def _set(self, side: dict[int, int], price: int, qty: int, *, is_bid: bool) -> None:
        if qty == 0:
            if side.pop(price, None) is not None:
                if is_bid and price == self._best_bid:
                    self._bid_dirty = True
                elif not is_bid and price == self._best_ask:
                    self._ask_dirty = True
            return
        side[price] = qty
        if is_bid:
            if not self._bid_dirty and (self._best_bid is None or price > self._best_bid):
                self._best_bid = price
        elif not self._ask_dirty and (self._best_ask is None or price < self._best_ask):
            self._best_ask = price

    @property
    def best_bid(self) -> int | None:
        if self._bid_dirty or (self._best_bid is None and self.bids):
            self._best_bid = max(self.bids) if self.bids else None
            self._bid_dirty = False
        return self._best_bid

    @property
    def best_ask(self) -> int | None:
        if self._ask_dirty or (self._best_ask is None and self.asks):
            self._best_ask = min(self.asks) if self.asks else None
            self._ask_dirty = False
        return self._best_ask

    @property
    def is_crossed(self) -> bool:
        bb, ba = self.best_bid, self.best_ask
        return bb is not None and ba is not None and bb >= ba

    def spread_ticks(self) -> int | None:
        bb, ba = self.best_bid, self.best_ask
        if bb is None or ba is None:
            return None
        return (ba - bb) // self.price_scale.step_units

    def depth(self) -> tuple[int, int]:
        return len(self.bids), len(self.asks)
