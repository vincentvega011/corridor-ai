"""Deterministic deadline resolver: one test per phrase type, plus never-guess cases (P3-1)."""

from datetime import date

import pytest

from corridor.contracts.intent import IntentType, LanguageTag
from corridor.contracts.llm_outputs import ParserOutput
from corridor.nlu.deadline import DeadlineStatus, resolve_deadline
from corridor.nlu.lang_pack import load_packs
from corridor.nlu.normalise import normalise
from corridor.nlu.slots import build_slots

TODAY = date(2026, 10, 10)  # a Saturday
PACKS = load_packs()


def res(text: str, today: date = TODAY, tz: str = "Asia/Kathmandu"):  # type: ignore[no-untyped-def]
    return resolve_deadline(text, today, tz, normalise(text, today, PACKS))


@pytest.mark.parametrize(
    ("text", "day"),
    [
        ("send 20k to Ama before Dashain", date(2026, 10, 21)),  # Vijaya Dashami
        ("send 15000 to dad before Dashain starts", date(2026, 10, 11)),  # Ghatasthapana
        ("ghatasthapana aghi Ama lai 10 hajar pathau", date(2026, 10, 11)),
        ("दशैँ अगाडि आमालाई २० हजार पठाउ", date(2026, 10, 21)),
        ("send 30k to bahini before Tihar", date(2026, 11, 8)),
        ("तिहार अगाडि दाइलाई ४० हजार पठाउ", date(2026, 11, 8)),
        ("pay 500 AUD to Priya by next Friday", date(2026, 10, 23)),
        ("send 800 QAR to Ama by this Friday", date(2026, 10, 16)),
        ("pay the rent by end of the month", date(2026, 10, 31)),
        ("mahina ko ant samma Ama lai 20 hajar pathau", date(2026, 10, 31)),
        ("महिनाको अन्तसम्म आमालाई १५ हजार पठाउ", date(2026, 10, 31)),
        ("send 50k to dai within 3 days", date(2026, 10, 13)),
        ("3 din bhitra Ama lai 8 hajar pathau", date(2026, 10, 13)),
        ("३ दिन भित्र आमालाई पैसा पठाउ", date(2026, 10, 13)),
        ("send 5k within 2 weeks", date(2026, 10, 24)),
        ("pay 6500 AUD before semester starts", date(2027, 2, 22)),
        ("semester suru hunu agadi 6500 AUD university lai tirnu", date(2027, 2, 22)),
        ("pay 6500 AUD by 15 Nov", date(2026, 11, 15)),
        ("send money to Ama by 20/10/2026", date(2026, 10, 20)),
    ],
)
def test_phrase_types_resolve_to_a_date(text: str, day: date) -> None:
    r = res(text)
    assert r.status is DeadlineStatus.RESOLVED
    assert r.on == day


def test_end_of_month_rolls_correctly_in_december_and_leap_february() -> None:
    assert res("by end of the month", date(2026, 12, 5)).on == date(2026, 12, 31)
    assert res("by end of the month", date(2028, 2, 3)).on == date(2028, 2, 29)


def test_deadline_is_end_of_day_in_the_users_time_zone() -> None:
    r = res("within 3 days", tz="Australia/Melbourne")
    assert r.at is not None
    assert r.at.isoformat() == "2026-10-13T23:59:59+11:00"
    assert res("within 3 days").at.isoformat() == "2026-10-13T23:59:59+05:45"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("send 25k to Ama by 2083-07-01 BS", "bikram_sambat_unsupported"),
        ("२०८३ कार्तिक १ सम्म आमालाई १० हजार पठाउ", "bikram_sambat_unsupported"),
        ("send 10k to Ama before the festival", "unknown_event"),
        ("send 10k to Ama before the holidays", "unknown_event"),
        ("send 5k within 4000 days", "days_out_of_range"),
    ],
)
def test_unknown_or_unsupported_phrases_ask_and_never_guess(text: str, reason: str) -> None:
    r = res(text)
    assert r.status is DeadlineStatus.NEEDS_CLARIFICATION
    assert r.reason == reason
    assert r.on is None


def test_semester_start_unknown_after_the_last_listed_start_asks() -> None:
    r = res("pay before semester starts", date(2027, 12, 1))
    assert r.status is DeadlineStatus.NEEDS_CLARIFICATION
    assert r.reason == "semester_start_unknown"


def test_no_deadline_cue_gives_none_and_resolution_is_deterministic() -> None:
    assert res("send 5k to Ama").status is DeadlineStatus.NONE
    assert res("within 3 days") == res("within 3 days")


def test_slot_builder_asks_instead_of_using_a_model_guess_for_an_unresolved_phrase() -> None:
    text = "send 10k to Ama before the festival"
    model = ParserOutput(
        language=LanguageTag.EN, intent=IntentType.SEND_MONEY, confidence_bps=9000,
        slots={"deadline": "2026-10-12"},  # a model guess must not be used  # type: ignore[dict-item]
    )  # fmt: skip
    built = build_slots(model, normalise(text, TODAY, PACKS), PACKS)
    assert "deadline" in built.ambiguous
    assert not any(k.value == "deadline" for k in built.slots)


def test_slot_builder_carries_the_resolved_day_and_end_of_day() -> None:
    text = "send 50k to dai within 3 days"
    out = ParserOutput(language=LanguageTag.EN, intent=IntentType.SEND_MONEY, confidence_bps=9000)
    built = build_slots(out, normalise(text, TODAY, PACKS), PACKS)
    dl = next(v for k, v in built.slots.items() if k.value == "deadline")
    assert dl.value["date"] == "2026-10-13"
    assert dl.value["at"].startswith("2026-10-13T23:59:59")
