"""A short imperative with an attached document means pay_invoice for that document (P2-4 part E)."""

import pytest

from corridor.nlu.lang_pack import load_packs
from corridor.nlu.rules import is_attachment_imperative

PACKS = load_packs()


@pytest.mark.parametrize(
    "text",
    [
        "pay this", "Pay this.", "please pay this", "pay it now", "PAY THAT",
        "yo tirdeu", "yo pay gardeu", "Yo pay gardeu hajur", "yo tirdinus",
        "इसलाई तिर्नु", "यो तिर्नुहोस्", "कृपया यो तिर्नुहोस्",
    ],
)  # fmt: skip
def test_short_imperatives_are_recognised(text: str) -> None:
    assert is_attachment_imperative(text, PACKS)


@pytest.mark.parametrize(
    "text",
    [
        "", "hello", "pay this to Ama", "pay 500 AUD", "pay this invoice to account 12345",
        "yo tirdeu 20 hajar", "send money to Ama", "इसलाई तिर्नु र अर्को खातामा पठाउ", "what is my balance",
    ],
)  # fmt: skip
def test_anything_longer_or_with_a_slot_is_not(text: str) -> None:
    assert not is_attachment_imperative(text, PACKS)
