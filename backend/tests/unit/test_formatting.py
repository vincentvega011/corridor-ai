"""Formatting helper: money is always ``CCY 1,234.56`` so a bare long integer never reaches a prompt."""

from datetime import date
from decimal import Decimal

import pytest

from corridor.contracts import CurrencyCode, Money
from corridor.formatting import format_bps, format_date, format_minor, format_money, format_rate
from corridor.llm.errors import PromptLeakError
from corridor.llm.guard import SecretRegistry, scan_text


@pytest.mark.parametrize(
    ("minor", "ccy", "expected"),
    [
        (100_000_000, CurrencyCode.NPR, "NPR 1,000,000.00"),
        (5, CurrencyCode.AUD, "AUD 0.05"),
        (123_456, CurrencyCode.QAR, "QAR 1,234.56"),
        (0, CurrencyCode.MYR, "MYR 0.00"),
        (99, CurrencyCode.CNY, "CNY 0.99"),
        (650_000, CurrencyCode.USD, "USD 6,500.00"),
        (-1_250, CurrencyCode.NPR, "-NPR 12.50"),
        (1, CurrencyCode.USD, "USD 0.01"),
        (99_999_999_999, CurrencyCode.NPR, "NPR 999,999,999.99"),
    ],
)
def test_format_minor(minor: int, ccy: CurrencyCode, expected: str) -> None:
    assert format_minor(minor, ccy) == expected
    assert format_money(Money(amount_minor=minor, currency=ccy)) == expected


@pytest.mark.parametrize("bad", [1.5, True, "100", None])
def test_format_minor_rejects_non_int(bad: object) -> None:
    with pytest.raises(TypeError):
        format_minor(bad, CurrencyCode.AUD)  # type: ignore[arg-type]


@pytest.mark.parametrize("ccy", list(CurrencyCode))
def test_formatted_money_passes_the_prompt_guard_but_raw_minor_does_not(ccy: CurrencyCode) -> None:
    reg = SecretRegistry()
    for minor in (100_000_000, 12_345_678_901 // 100, 5, 99_999_999_999):
        scan_text(f"balance {format_minor(minor, ccy)} as of {date(2026, 10, 10)}", reg)
    with pytest.raises(PromptLeakError):
        scan_text(f"balance {100_000_000}", reg)  # a bare 9 digit minor unit integer


def test_format_rate_bps_date() -> None:
    assert format_rate(Decimal("106.91000000")) == "106.9"  # 4 significant digits
    assert (
        format_rate(Decimal("0.009355")) == "0.009355"
        and format_rate(Decimal("0.00935")) == "0.009350"
    )
    assert format_rate(Decimal("0.0093455")) == "0.009346"  # banker's rounding
    assert format_rate(Decimal("0.0093465")) == "0.009346"
    assert format_rate(Decimal("12345.678")) == "12350" and format_rate(Decimal(0)) == "0"
    assert format_bps(125) == "1.25%"
    assert format_bps(0) == "0.00%"
    assert format_bps(5) == "0.05%"
    assert format_date(date(2026, 10, 31)) == "2026-10-31"
    scan_text(
        f"rate {format_rate(Decimal('106.91234567'))} on {format_date(date(2026, 10, 9))}",
        SecretRegistry(),
    )
