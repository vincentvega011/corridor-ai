"""Money, currencies and FX rates.

Security reason: money is the asset the whole system protects. Floats lose cents and make
canonical hashing ambiguous, so every amount is an integer in minor units and every FX
rate is a quantised Decimal. Validation is explicit (not Pydantic coercion) so that a
model or document that emits ``12.5``, ``"12"`` or ``true`` as an amount is rejected
instead of being silently converted into a value the user never saw (INV-5, INV-10).
"""

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict


class CurrencyCode(StrEnum):
    """Supported ISO 4217 currencies. A currency outside this set cannot be represented."""

    NPR = "NPR"
    AUD = "AUD"
    QAR = "QAR"
    MYR = "MYR"
    CNY = "CNY"
    USD = "USD"


MINOR_UNIT_EXPONENT: dict[CurrencyCode, int] = {
    CurrencyCode.NPR: 2,
    CurrencyCode.AUD: 2,
    CurrencyCode.QAR: 2,
    CurrencyCode.MYR: 2,
    CurrencyCode.CNY: 2,
    CurrencyCode.USD: 2,
}
"""Digits after the decimal point per currency (ISO 4217 minor unit exponent)."""


def minor_unit_exponent(currency: CurrencyCode) -> int:
    """Return the number of minor unit digits for ``currency``."""
    return MINOR_UNIT_EXPONENT[currency]


def strict_int(value: Any) -> int:
    """Accept only a real ``int``. Rejects bool, float, Decimal and numeric strings."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"amount must be an integer number of minor units, got {type(value).__name__}"
        )
    return value


MinorUnits = Annotated[int, BeforeValidator(strict_int)]
"""An integer count of minor units. The only legal representation of an amount."""

RATE_QUANTUM = Decimal("1E-8")


def _to_rate(value: Any) -> Decimal:
    """Convert to a positive Decimal quantised to 8 places (ROUND_HALF_EVEN). Floats rejected."""
    if isinstance(value, bool | float):
        raise ValueError("rate must be a Decimal, int or string, never a float or bool")
    try:
        if isinstance(value, Decimal):
            dec = value
        elif isinstance(value, int | str):
            dec = Decimal(value)
        else:
            raise ValueError(f"rate has unsupported type {type(value).__name__}")
        if not dec.is_finite() or dec <= 0:
            raise ValueError("rate must be finite and positive")
        with localcontext() as ctx:
            ctx.prec = 60
            return dec.quantize(RATE_QUANTUM, rounding=ROUND_HALF_EVEN)
    except InvalidOperation as exc:
        raise ValueError("rate is not a valid decimal number") from exc


Rate = Annotated[Decimal, BeforeValidator(_to_rate)]
"""An FX rate or effective rate: Decimal, 8 places, ROUND_HALF_EVEN, strictly positive."""


class Money(BaseModel):
    """An amount in integer minor units plus its currency.

    Security reason: the pair is the unit that is hashed into a confirmation grant (INV-5)
    and compared by policy limits. Never a float; the validator rejects floats, bools and
    numeric strings so there is exactly one way to express an amount.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    amount_minor: MinorUnits
    currency: CurrencyCode
