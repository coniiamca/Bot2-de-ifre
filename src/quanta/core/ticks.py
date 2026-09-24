"""Exact fixed-point conversion between exchange decimal strings and integers.

Prices and quantities are held internally as integers (number of ``10**-decimals`` units)
to avoid float rounding errors and ``Decimal`` overhead on hot paths. Conversion happens only
at the exchange boundary, using the instrument's tick size / lot step.
"""

from __future__ import annotations

from decimal import Decimal


class ScaleError(ValueError):
    pass


def _decimals_of(step: str) -> int:
    d = Decimal(step)
    if d <= 0:
        raise ScaleError(f"step must be positive, got {step!r}")
    exponent = d.normalize().as_tuple().exponent
    assert isinstance(exponent, int)
    return max(0, -exponent)


class Scale:
    """Fixed-point scale derived from an exchange step (tick size or lot step).

    ``to_int("63500.10")`` with step ``"0.10"`` → ``635001`` (units of 0.1).
    """

    __slots__ = ("decimals", "factor", "step", "step_units")

    def __init__(self, step: str) -> None:
        self.decimals = _decimals_of(step)
        self.factor: int = int(10**self.decimals)
        self.step = Decimal(step).normalize()
        units = self.step * self.factor
        if units != units.to_integral_value():
            raise ScaleError(f"step {step!r} is not representable with {self.decimals} decimals")
        self.step_units = int(units)

    def to_int(self, text: str) -> int:
        """Parse a decimal string exactly. Raises if it has more precision than the scale."""
        neg = text.startswith("-")
        body = text[1:] if neg else text
        whole, _, frac = body.partition(".")
        frac = frac.rstrip("0")
        if len(frac) > self.decimals:
            raise ScaleError(f"{text!r} has more than {self.decimals} decimals")
        try:
            value = int(whole or "0") * self.factor + (
                int(frac.ljust(self.decimals, "0")) if self.decimals else 0
            )
        except ValueError as exc:
            raise ScaleError(f"invalid decimal {text!r}") from exc
        return -value if neg else value

    def to_str(self, value: int) -> str:
        if self.decimals == 0:
            return str(value)
        sign = "-" if value < 0 else ""
        whole, frac = divmod(abs(value), self.factor)
        return f"{sign}{whole}.{frac:0{self.decimals}d}"

    def is_aligned(self, value: int) -> bool:
        return value % self.step_units == 0

    def to_decimal(self, value: int) -> Decimal:
        return Decimal(value).scaleb(-self.decimals)
