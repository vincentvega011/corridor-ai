"""Names with titles, kinship suffixes and case suffixes (P2-5 part D). The rules are data in data/lang."""

from datetime import date

import pytest

from corridor.agent.payees import resolve_payee
from corridor.contracts import SlotName
from corridor.nlu.lang_pack import load_packs
from corridor.nlu.normalise import normalise
from corridor.nlu.slots import build_slots

PACKS = load_packs()
TODAY = date(2026, 10, 11)

PAYEES = [
    {"payee_id": "p_ramesh", "display_name": "Ramesh Thapa (brother, fictional)", "relation": "elder_brother"},
    {"payee_id": "p_sita", "display_name": "Sita (sister, fictional)", "relation": "elder_sister"},
    {"payee_id": "p_chen", "display_name": "Mr. Chen (landlord, fictional)", "relation": "landlord"},
    {"payee_id": "p_kumar", "display_name": "Kumar (tutor, fictional)", "relation": None},
]  # fmt: skip


def _ref(text: str) -> dict:  # type: ignore[type-arg]
    b = build_slots(None, normalise(text, TODAY, PACKS), PACKS)
    assert not b.ambiguous.get("payee_ref"), b.ambiguous
    slot = b.slots[SlotName.PAYEE_REF]
    return slot.value  # type: ignore[no-any-return]


@pytest.mark.parametrize(
    ("text", "name", "payee_id"),
    [
        ("Send Ramesh dai 20 hajar", "Ramesh dai", "p_ramesh"),
        ("Sita didi lai 5000 pathau", "Sita didi", "p_sita"),
        ("Pay Mr Chen AUD 1800", "Mr Chen", "p_chen"),
        ("Kumar sir lai 2000 pathau", "Kumar sir", "p_kumar"),
        ("रमेशलाई २० हजार पठाउ", "रमेश", None),
        ("सीतालाई ५००० पठाउ", "सीता", None),
        ("रमेशको खातामा १० हजार पठाउ", "रमेश", None),
        ("Rameshlai 20 hajar pathau", "Ramesh", "p_ramesh"),
        ("Ramesh lai 20 hajar pathau", "Ramesh", "p_ramesh"),
        ("Ramesh ko lai 5 hajar pathau", "Ramesh", "p_ramesh"),
    ],
)
def test_name_is_found_without_its_decoration_and_without_a_clarification(
    text: str, name: str, payee_id: str | None
) -> None:
    ref = _ref(text)
    assert ref["text"] == name and ref["relation"] is None
    if payee_id:
        r = resolve_payee(ref, PAYEES, PACKS)
        assert r.status == "one" and r.payee and r.payee["payee_id"] == payee_id


def test_a_title_or_honorific_alone_is_never_a_name() -> None:
    for t in ("sir", "Mr", "dai", "Dr"):
        assert normalise(t, TODAY, PACKS).names == ()


def test_a_bare_kinship_word_is_still_a_relation_not_a_name() -> None:
    b = build_slots(None, normalise("Ama lai 5 hajar pathau", TODAY, PACKS), PACKS)
    assert b.slots[SlotName.PAYEE_REF].value == {"text": "Ama", "relation": "mother"}


def test_two_different_people_are_still_ambiguous() -> None:
    b = build_slots(None, normalise("Ramesh dai ra Sita didi lai 5 hajar", TODAY, PACKS), PACKS)
    assert "payee_ref" in b.ambiguous


def test_a_lower_case_word_ending_in_ma_or_le_is_not_a_glued_name() -> None:
    for t in ("send the karma 500 aud", "pay the table 500 aud"):
        assert normalise(t, TODAY, PACKS).names == ()


def test_the_title_does_not_make_a_stranger_match() -> None:
    assert resolve_payee({"text": "Mr Bob", "relation": None}, PAYEES, PACKS).status == "none"
    assert (
        resolve_payee({"text": "Ramesh dai", "relation": None}, PAYEES[1:], PACKS).status == "none"
    )


def test_spans_stay_inside_the_original_text_and_match_the_name() -> None:
    n = normalise("रमेशलाई २० हजार पठाउ", TODAY, PACKS)
    assert n.names[0].span.text == "रमेश" and n.names[0].span.start == 0


@pytest.mark.parametrize("name", ["Karki", "Sharma", "Thapa", "Rai", "Gurung", "Chen"])
def test_a_name_that_ends_like_a_case_marker_is_kept_whole(name: str) -> None:
    n = normalise(f"{name} lai 5 hajar pathau", TODAY, PACKS)
    assert [x.name for x in n.names] == [name]
    n = normalise(f"Send {name} 5 hajar", TODAY, PACKS)
    assert [x.name for x in n.names] == [name]
