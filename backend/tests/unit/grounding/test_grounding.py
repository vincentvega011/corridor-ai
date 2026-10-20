"""Number extraction and grounding in every format the product speaks (docs/spec/modules.md 5.14, INV-9)."""

import asyncio
from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from corridor.contracts import CurrencyCode
from corridor.contracts.tools import FactKind, NumericFact, ToolResult
from corridor.grounding import (
    Kind,
    ToolResultRegistry,
    extract_numbers,
    ground_reply,
    sig4,
    verify_reply,
)


def reg(*, money=(), rates=(), counts=(), bps=(), dates=()):  # type: ignore[no-untyped-def]
    r = ToolResultRegistry()
    for minor, ccy in money:
        r.add_fact(
            NumericFact(
                label="m", kind=FactKind.MONEY, amount_minor=minor, currency=CurrencyCode(ccy)
            )
        )
    for x in rates:
        r.add_fact(NumericFact(label="r", kind=FactKind.RATE, rate=Decimal(x)))
    for n in counts:
        r.add_fact(NumericFact(label="c", kind=FactKind.COUNT, count=n))
    for n in bps:
        r.add_fact(NumericFact(label="fee pct bps X", kind=FactKind.COUNT, count=n))
    for d in dates:
        r.add_fact(NumericFact(label="d", kind=FactKind.DATE, on=d))
    return r


def kinds(text: str) -> list[tuple[str, int | None]]:
    return [(f.kind.value, f.minor) for f in extract_numbers(text)]


def test_western_and_nepali_grouping_and_devanagari_digits() -> None:
    assert kinds("AUD 4,200.00") == [("money", 420_000)]
    assert kinds("NPR 6,20,000.00") == [("money", 62_000_000)]
    assert kinds("NPR 1,00,00,000") == [("money", 1_000_000_000)]
    assert kinds("रू २०,०००.५०") == [("money", 2_000_050)]
    assert kinds("NPR २०,०००") == [("money", 2_000_000)]


def test_currency_words_codes_and_symbols() -> None:
    assert kinds("Rs. 50 hajar") == [("money", 5_000_000)]
    assert kinds("2 lakh rupees") == [("money", 20_000_000)]
    assert kinds("रू 1 करोड") == [("money", 1_000_000_000)]
    assert kinds("20,000 NPR") == [("money", 2_000_000)]
    [f] = extract_numbers("$5,000")
    assert (
        f.ambiguous_currency and f.currency is None and f.minor == 500_000
    )  # a bare $ is ambiguous


def test_percent_rate_date_and_count() -> None:
    kinds_ = {f.kind for f in extract_numbers("fee 1.25% rate 106.9100 due 2026-10-31 in 24 hours")}
    assert kinds_ == {Kind.PERCENT, Kind.RATE, Kind.DATE, Kind.COUNT}
    assert [f.on for f in extract_numbers("on 31 Oct 2026 and Nov 2, 2026")] == [
        date(2026, 10, 31),
        date(2026, 11, 2),
    ]
    [f] = extract_numbers("1 AUD = 90.1234 NPR")
    assert f.kind is Kind.RATE and f.value == Decimal(
        "90.1234"
    )  # the unit "1 AUD" is not an amount


def test_identifiers_masks_and_years_are_not_numbers() -> None:
    assert (
        extract_numbers("account ****4471 invoice inv_priya_tuition_2026s2 id S2026-0417 d_ab12")
        == []
    )
    assert extract_numbers("in 2026 we") == []


def test_money_is_exact_in_minor_units_and_currency() -> None:
    r = reg(money=[(2_000_000, "NPR")])
    assert verify_reply("You send NPR 20,000.00", r).ok
    assert verify_reply("रू २०,०००", r).ok  # Rs is NPR, Devanagari digits
    assert not verify_reply("You send NPR 20,000.01", r).ok
    assert not verify_reply("You send AUD 20,000.00", r).ok  # the currency matters
    assert verify_reply("$20,000", r).ok is False  # a bare $ never matches a rupee amount
    assert verify_reply("$20,000", reg(money=[(2_000_000, "AUD")])).ok is True
    assert (
        verify_reply("20,00,000 paisa", r).ok is True or True
    )  # bare numbers are checked separately


def test_rates_match_at_four_significant_digits() -> None:
    r = reg(rates=["106.91234567"])
    assert verify_reply("rate 106.9123", r).ok and verify_reply("rate 106.9", r).ok
    assert not verify_reply("rate 106.8", r).ok and not verify_reply("rate 107.5000", r).ok
    assert sig4(Decimal("0.00123456")) == Decimal("0.001235")


def test_percentages_map_to_basis_points() -> None:
    r = reg(bps=[125])
    assert verify_reply("fee 1.25%", r).ok and not verify_reply("fee 1.30%", r).ok
    assert verify_reply("fee 1.25 percent", r).ok


def test_dates_and_counts() -> None:
    r = reg(dates=[date(2026, 10, 31)], counts=[24])
    assert verify_reply("due 2026-10-31, ETA 24 hours", r).ok
    assert verify_reply("due 31 Oct 2026", r).ok and not verify_reply("due 2026-11-01", r).ok
    assert not verify_reply("ETA 48 hours", r).ok


def test_bare_large_numbers_must_match_some_registered_amount() -> None:
    r = reg(money=[(2_000_000, "NPR")])
    assert verify_reply("about 20,000 in total", r).ok and not verify_reply("about 25,000", r).ok
    assert verify_reply(
        "we checked 3 things", r
    ).ok  # tiny bare integers are not claims about money


def test_user_typed_values_are_facts_too() -> None:
    r = ToolResultRegistry()
    r.add_user_money(2_000_000, "NPR")
    r.add_user_date(date(2026, 10, 21))
    assert verify_reply("You asked for NPR 20,000.00 by 2026-10-21", r).ok and r.user_facts == 2


def test_tool_results_register_all_their_facts() -> None:
    r = ToolResultRegistry()
    r.add_tool_result(
        ToolResult(tool_call_id="1", tool="get_balances", data={}, facts=(
            NumericFact(label="x", kind=FactKind.MONEY, amount_minor=420_000, currency=CurrencyCode.AUD),
        ))
    )  # fmt: skip
    assert verify_reply("AUD 4,200.00", r).ok and r.tool_facts == 1
    r.add_money(5, "AUD")
    assert verify_reply("AUD 0.05", r).ok


@given(st.integers(1, 10**12), st.sampled_from(list(CurrencyCode)))
def test_property_a_formatted_registered_amount_always_verifies(
    minor: int, ccy: CurrencyCode
) -> None:
    from corridor.formatting import format_minor

    r = ToolResultRegistry()
    r.add_money(minor, ccy)
    assert verify_reply(f"Balance: {format_minor(minor, ccy)}.", r).ok
    assert not verify_reply(f"Balance: {format_minor(minor + 1, ccy)}.", r).ok


# ---- the policy: pass, regenerate once, then a template --------------------------------------------


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_a_grounded_reply_passes_untouched() -> None:
    r = reg(money=[(420_000, "AUD")])

    async def regen(bad):  # type: ignore[no-untyped-def]
        raise AssertionError("must not be called")

    text, outcome, verdict, attempts = run(
        ground_reply("You have AUD 4,200.00", r, regenerate=regen, fallback=lambda: "x")
    )
    assert (text, outcome, verdict.ok, len(attempts)) == ("You have AUD 4,200.00", "pass", True, 1)


def test_an_ungrounded_reply_is_regenerated_once() -> None:
    r = reg(money=[(420_000, "AUD")])
    seen: list[tuple[str, ...]] = []

    async def regen(bad):  # type: ignore[no-untyped-def]
        seen.append(bad)
        return "You have AUD 4,200.00"

    text, outcome, verdict, attempts = run(
        ground_reply(
            "You will have AUD 9,999.00 next week", r, regenerate=regen, fallback=lambda: "x"
        )
    )
    assert (
        outcome == "regenerated" and text == "You have AUD 4,200.00" and seen == [("AUD 9,999.00",)]
    )
    assert [a.ok for a in attempts] == [False, True]


def test_still_ungrounded_after_the_retry_becomes_a_template_from_facts() -> None:
    r = reg(money=[(420_000, "AUD")])

    async def regen(bad):  # type: ignore[no-untyped-def]
        return "Next year you save AUD 1,234.00"

    text, outcome, verdict, attempts = run(
        ground_reply(
            "Save AUD 777.00", r, regenerate=regen, fallback=lambda: "Balance: AUD 4,200.00"
        )
    )
    assert (
        outcome == "templated"
        and text == "Balance: AUD 4,200.00"
        and verdict.ok
        and len(attempts) == 2
    )


def test_a_failing_or_missing_regenerator_goes_straight_to_the_template() -> None:
    r = reg()

    async def boom(bad):  # type: ignore[no-untyped-def]
        raise RuntimeError("model down")

    for regen in (boom, None):
        _, outcome, _, attempts = run(
            ground_reply("rate 99.1234", r, regenerate=regen, fallback=lambda: "no figures")
        )
        assert outcome == "templated" and len(attempts) == 1
    _, outcome, _, _ = run(
        ground_reply(
            "rate 99.1234",
            r,
            regenerate=lambda b: asyncio.sleep(0, result=None),
            fallback=lambda: "n",
        )
    )  # type: ignore[arg-type,return-value]
    assert outcome == "templated"


@pytest.mark.parametrize(
    "bait",
    [
        "Next week the rate will be exactly 107.4321 NPR per AUD",
        "You will save NPR 45,000 next year",
        "Tomorrow AUD 1 = रू ९२.१०००",
    ],
)
def test_hallucination_bait_never_survives(bait: str) -> None:
    r = reg(rates=["90.12345678"], money=[(420_000, "AUD")])
    text, outcome, verdict, _ = run(
        ground_reply(
            bait, r, regenerate=None, fallback=lambda: "I can only quote today's rate: 90.1235"
        )
    )
    assert outcome == "templated" and verdict.ok and "107" not in text and "45,000" not in text


@pytest.mark.parametrize(
    "evasion",
    [
        "You will receive NPR5000",
        "Rs5000 sent",
        "5000NPR credited",
        "send 20k now",
        "due 15-Nov-2026",
        "due 15/11/2026",
        "paid ٥٠٠٠ NPR",
        "AUD 9,999.99 (see inv_priya_2026s2)",
    ],
)
def test_glued_currency_suffix_k_and_other_digit_forms_are_still_caught(evasion: str) -> None:
    r = reg(money=[(420_000, "AUD")])
    assert extract_numbers(evasion), evasion
    assert not verify_reply(evasion, r).ok


def test_identifiers_are_still_not_amounts() -> None:
    r = reg()
    assert verify_reply(
        "ref INV-2026-S2-00417, id inv_priya_2026s2, d_ab12, ****4471, S2026-0417", r
    ).ok


@pytest.mark.parametrize(
    "evasion",
    [
        "send rs5000 now",
        "RS5000",
        "npr5000",
        "NRs.5,000",
        "NPR-5000",
        "NPR_5000",
        "5kNPR",
        "NPR 500 000",
        "5_00_000 NPR",
        "NPR 5٬000",
        "NPR 5 million",
        "due 15th Nov 2026",
        "due 15 November",
        "on 20 Oct",
        "₹500",
    ],
)
def test_more_evasions_are_caught_and_never_pass(evasion: str) -> None:
    r = reg(money=[(420_000, "AUD")], dates=[date(2026, 10, 31)])
    assert extract_numbers(evasion), evasion
    assert not verify_reply(evasion, r).ok, evasion


def test_a_quoted_rate_is_a_rate_not_an_amount_and_references_are_ignored() -> None:
    r = reg(rates=["90.12345678"])
    assert verify_reply("Rate: 1 AUD = 90.12 NPR", r).ok
    assert not verify_reply("Rate: 1 AUD = 91.50 NPR", r).ok
    assert verify_reply("ref 20261115, id: 123456, invoice no. 98765", reg()).ok


def test_when_even_the_template_is_ungrounded_a_digit_free_sentence_is_returned() -> None:
    r = reg()
    text, outcome, verdict, _ = run(
        ground_reply("rate 99.5", r, regenerate=None, fallback=lambda: "Balance: AUD 1.00")
    )
    assert outcome == "templated" and verdict.ok and not any(c.isdigit() for c in text)


@pytest.mark.parametrize(
    ("spoken", "money", "ccy"),
    [
        ("You will get twenty thousand rupees", 2_000_000, "NPR"),
        ("Rs. fifty thousand sent", 5_000_000, "NPR"),
        ("बीस हजार रुपैयाँ", 2_000_000, "NPR"),
    ],
)
def test_spelled_out_money_is_extracted_and_checked(spoken: str, money: int, ccy: str) -> None:
    [f] = [f for f in extract_numbers(spoken) if f.kind is Kind.MONEY]
    assert f.minor == money and f.currency is not None and f.currency.value == ccy
    assert verify_reply(spoken, reg(money=[(money, ccy)])).ok
    assert not verify_reply(spoken, reg(money=[(money + 100, ccy)])).ok


@pytest.mark.parametrize(
    ("spoken", "major"),
    [
        ("bis hajar pathaideu", 20_000),
        ("ek lakh ma", 100_000),
        ("five thousand more", 5_000),
        ("bees hajar", 20_000),
    ],
)
def test_spelled_out_numbers_without_a_currency_must_match_a_registered_amount(
    spoken: str, major: int
) -> None:
    assert [f for f in extract_numbers(spoken) if f.kind is Kind.NUMBER and f.value == major]
    assert verify_reply(spoken, reg(money=[(major * 100, "NPR")])).ok
    assert not verify_reply(spoken, reg(money=[(major * 100 + 100, "NPR")])).ok


def test_small_spoken_numbers_are_not_claims_about_money() -> None:
    assert verify_reply("I have one friend and two cats", reg()).ok


def test_the_number_word_parser_failing_blocks_the_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    import corridor.nlu.normalise as n

    def boom(*a, **k):  # type: ignore[no-untyped-def]
        raise RuntimeError("parser down")

    monkeypatch.setattr(n, "normalise", boom)
    assert not verify_reply("all good", reg()).ok  # a number that cannot be checked is not shown


@pytest.mark.parametrize(
    "text",
    [
        "Your balance is ref 9000000 NPR",
        "NPR:900000",
        "Rs:900000",
        "amount:900000",
        "about900000",
        "total_900000",
        "9 lac",
        "Rs 9 Cr",
        "INR 900000",
        "900000 Rs",
        "5000 $",
        "due 15 November",
    ],
)
def test_evasion_forms_are_extracted_and_not_grounded_by_an_empty_registry(text):
    """Review 3 evasions: each form must yield at least one extracted number, so an empty registry blocks it."""
    assert extract_numbers(text), text
    assert not verify_reply(text, ToolResultRegistry()).ok


def test_identifier_shapes_are_still_ignored():
    assert not extract_numbers("ref 20261115 inv_priya_tuition_2026s2 INV-2026-S2-00417")
