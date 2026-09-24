from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from quanta.core.ticks import Scale, ScaleError


def test_basic_conversion() -> None:
    s = Scale("0.10")
    assert s.decimals == 1
    assert s.to_int("63500.10") == 635001
    assert s.to_int("63500.1000000") == 635001
    assert s.to_str(635001) == "63500.1"
    assert s.step_units == 1


def test_step_not_power_of_ten() -> None:
    s = Scale("0.5")
    assert s.to_int("10.5") == 105
    assert s.is_aligned(105)
    assert not s.is_aligned(103)


def test_integer_step() -> None:
    s = Scale("1")
    assert s.to_int("42") == 42
    assert s.to_str(42) == "42"
    with pytest.raises(ScaleError):
        s.to_int("42.5")


def test_rejects_excess_precision_and_garbage() -> None:
    s = Scale("0.01")
    with pytest.raises(ScaleError):
        s.to_int("1.001")
    with pytest.raises(ScaleError):
        s.to_int("abc")
    with pytest.raises(ScaleError):
        Scale("0")


def test_negative() -> None:
    s = Scale("0.001")
    assert s.to_int("-1.5") == -1500
    assert s.to_str(-1500) == "-1.500"


@given(st.integers(min_value=-(10**15), max_value=10**15), st.integers(min_value=0, max_value=8))
def test_roundtrip(value: int, decimals: int) -> None:
    step = "1" if decimals == 0 else "0." + "0" * (decimals - 1) + "1"
    s = Scale(step)
    assert s.to_int(s.to_str(value)) == value
    assert s.to_decimal(value) == Decimal(s.to_str(value))
