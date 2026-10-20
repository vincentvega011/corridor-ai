from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

from corridor.contracts import CurrencyCode, Money, Rate, minor_unit_exponent


@pytest.mark.parametrize("bad", [12.5, 12.0, True, False, "12.5", "12", Decimal("12"), None])
def test_money_rejects_non_int(bad: object) -> None:
    with pytest.raises(ValidationError):
        Money(amount_minor=bad, currency="AUD")  # type: ignore[arg-type]


def test_money_accepts_int() -> None:
    m = Money(amount_minor=12_500, currency="NPR")  # type: ignore[arg-type]
    assert m.amount_minor == 12_500 and m.currency is CurrencyCode.NPR


def test_money_json_roundtrip() -> None:
    m = Money(amount_minor=5, currency=CurrencyCode.USD)
    assert Money.model_validate_json(m.model_dump_json()) == m


@pytest.mark.parametrize("cur", ["EUR", "BTC", "", "npr"])
def test_unsupported_currency(cur: str) -> None:
    with pytest.raises(ValidationError):
        Money(amount_minor=1, currency=cur)  # type: ignore[arg-type]


def test_exponents_cover_all_currencies() -> None:
    assert all(minor_unit_exponent(c) == 2 for c in CurrencyCode)


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        Money(amount_minor=1, currency="AUD", note="x")  # type: ignore[call-arg]


def test_rate_quantised_half_even() -> None:
    ta = TypeAdapter(Rate)
    assert ta.validate_python("1.000000005") == Decimal("1.00000000")  # half even to even digit
    assert ta.validate_python("1.000000015") == Decimal("1.00000002")
    assert ta.validate_python(2) == Decimal("2.00000000")


@pytest.mark.parametrize("bad", [1.5, True, "abc", "NaN", "Infinity", 0, -1, "0", [1]])
def test_rate_rejects(bad: object) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(Rate).validate_python(bad)
