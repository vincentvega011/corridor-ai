"""A quote or comparison names two currencies by design; that is not a currency ambiguity."""

from datetime import date

from corridor.contracts.intent import IntentType, SlotName
from corridor.contracts.llm_outputs import ParserOutput
from corridor.contracts.provenance import Provenance
from corridor.nlu.lang_pack import load_packs
from corridor.nlu.normalise import normalise
from corridor.nlu.slots import build_slots

TEXT = "What is today's AUD to NPR rate, and how much NPR would AUD 200 become?"


def _out(intent: IntentType) -> ParserOutput:
    return ParserOutput(
        language="en", intent=intent, slots={}, missing_slots=(), confidence_bps=9500
    )


def test_convert_with_two_currencies_is_not_ambiguous() -> None:
    packs = load_packs()
    b = build_slots(
        _out(IntentType.CONVERT_CURRENCY), normalise(TEXT, date(2026, 10, 11), packs), packs
    )
    assert "currency" not in b.ambiguous
    cur = b.slots[SlotName.CURRENCY]
    assert cur.value == "AUD" and cur.provenance is Provenance.USER_TYPED


def test_send_money_with_two_currencies_is_still_ambiguous() -> None:
    packs = load_packs()
    b = build_slots(_out(IntentType.SEND_MONEY), normalise(TEXT, date(2026, 10, 11), packs), packs)
    assert b.ambiguous["currency"] == ("AUD", "NPR")
