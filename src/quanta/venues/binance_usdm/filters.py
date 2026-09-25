"""Symbol trading rules from exchangeInfo: price tick, quantity step, minimum quantity and
notional. Prices and quantities are exact decimals, rounded in the safe direction (a buy
limit never rounds up past the intended price, quantities never round up past a limit)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class SymbolRules:
    symbol: str
    status: str
    tick: Decimal
    step: Decimal
    min_qty: Decimal
    max_qty: Decimal
    market_step: Decimal
    market_max_qty: Decimal
    min_notional: Decimal

    def price(self, value: float | Decimal, rounding: str) -> Decimal:
        """Round to the tick: ``down`` (a buy limit) or ``up`` (a sell limit)."""
        mode = ROUND_FLOOR if rounding == "down" else ROUND_CEILING
        return (Decimal(str(value)) / self.tick).to_integral_value(mode) * self.tick

    def qty(self, value: float | Decimal, market: bool = False) -> Decimal:
        step = self.market_step if market else self.step
        return (Decimal(str(value)) / step).to_integral_value(ROUND_FLOOR) * step

    def qty_for_notional(self, notional: float, price: float) -> Decimal:
        """The smallest step multiple whose notional at ``price`` reaches ``notional``."""
        q = (Decimal(str(notional)) / Decimal(str(price)) / self.step).to_integral_value(
            ROUND_CEILING
        ) * self.step
        return max(q, self.min_qty)

    def check(self, qty: Decimal, price: float, market: bool = False) -> str | None:
        """Why the exchange would reject this order, or None."""
        if self.status != "TRADING":
            return f"{self.symbol} is {self.status}"
        step = self.market_step if market else self.step
        if qty <= 0 or qty % step != 0:
            return f"quantity {qty} is not a positive multiple of {step}"
        if qty < self.min_qty:
            return f"quantity {qty} < minimum {self.min_qty}"
        if qty > (self.market_max_qty if market else self.max_qty):
            return f"quantity {qty} above the maximum"
        if qty * Decimal(str(price)) < self.min_notional:
            return f"notional {qty * Decimal(str(price)):.2f} < minimum {self.min_notional}"
        return None


def fmt(value: Decimal) -> str:
    """Plain decimal string without exponent or trailing zeros."""
    return format(value.normalize(), "f")


def parse_rules(info: dict[str, Any]) -> dict[str, SymbolRules]:
    out: dict[str, SymbolRules] = {}
    for s in info.get("symbols", []):
        f = {x["filterType"]: x for x in s.get("filters", [])}
        lot = f.get("LOT_SIZE", {})
        mlot = f.get("MARKET_LOT_SIZE", lot)
        out[s["symbol"]] = SymbolRules(
            symbol=s["symbol"],
            status=s.get("status", "TRADING"),
            tick=Decimal(f["PRICE_FILTER"]["tickSize"]),
            step=Decimal(lot["stepSize"]),
            min_qty=Decimal(lot.get("minQty", lot["stepSize"])),
            max_qty=Decimal(lot.get("maxQty", "1e12")),
            market_step=Decimal(mlot.get("stepSize", lot["stepSize"])),
            market_max_qty=Decimal(mlot.get("maxQty", lot.get("maxQty", "1e12"))),
            min_notional=Decimal(f.get("MIN_NOTIONAL", {}).get("notional", "0")),
        )
    return out
