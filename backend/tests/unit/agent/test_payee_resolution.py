"""Deterministic payee resolution: zero, one or many matches, against the user's own payees only."""

from corridor.agent.payees import resolve_payee
from corridor.nlu.lang_pack import load_packs

PACKS = load_packs()
PAYEES = [
    {"payee_id": "p_ama", "display_name": "Ama (mother, fictional)", "relation": "mother"},
    {"payee_id": "p_sur", "display_name": "Suresh (younger brother, fictional)", "relation": "younger_brother"},
    {"payee_id": "p_chen", "display_name": "Mr. Chen (landlord, fictional)", "relation": "landlord"},
    {"payee_id": "p_par", "display_name": "Aama and Baba (parents, fictional)", "relation": "parents"},
]  # fmt: skip


def test_unknown_name_is_none() -> None:
    assert resolve_payee({"text": "Bob", "relation": None}, PAYEES, PACKS).status == "none"


def test_relation_match() -> None:
    r = resolve_payee({"text": "Ama", "relation": "mother"}, PAYEES, PACKS)
    assert r.status in {"one", "many"} and "p_ama" in {m["payee_id"] for m in r.matches}


def test_multi_word_name_with_title() -> None:
    r = resolve_payee({"text": "Mr Chen", "relation": None}, PAYEES, PACKS)
    assert r.status == "one" and r.payee and r.payee["payee_id"] == "p_chen"


def test_every_typed_word_must_match() -> None:
    assert resolve_payee({"text": "Mr Bob", "relation": None}, PAYEES, PACKS).status == "none"


def test_parents_cover_mother_or_father() -> None:
    r = resolve_payee({"text": "Baba", "relation": "father"}, PAYEES, PACKS)
    assert "p_par" in {m["payee_id"] for m in r.matches}


def test_two_matches_is_many() -> None:
    twice = [*PAYEES, {"payee_id": "p_ama2", "display_name": "Ama Devi (aunt)", "relation": "aunt"}]
    assert resolve_payee({"text": "Ama", "relation": None}, twice, PACKS).status == "many"


def test_payees_are_only_the_list_given() -> None:
    assert resolve_payee({"text": "Ama", "relation": "mother"}, [], PACKS).status == "none"


def test_two_payees_that_differ_only_by_a_title_or_suffix_are_asked_about_not_picked() -> None:
    two = [
        {"payee_id": "p_mr", "display_name": "Mr Chen (landlord)", "relation": "landlord"},
        {"payee_id": "p_dr", "display_name": "Dr Chen (doctor)", "relation": None},
    ]
    for typed in ("Chen", "Mr Chen", "Dr Chen"):  # the title does not choose between two people
        assert resolve_payee({"text": typed, "relation": None}, two, PACKS).status == "many"
    sibs = [
        {"payee_id": "p_dai", "display_name": "Ramesh dai", "relation": None},
        {"payee_id": "p_bhai", "display_name": "Ramesh bhai", "relation": None},
    ]
    for typed in ("Ramesh", "Ramesh dai", "Ramesh bhai"):
        assert resolve_payee({"text": typed, "relation": None}, sibs, PACKS).status == "many"


def test_a_different_title_than_the_one_stored_is_asked_not_assumed() -> None:
    sole = [{"payee_id": "p_dr", "display_name": "Dr Chen (doctor)", "relation": None}]
    assert resolve_payee({"text": "Mr Chen", "relation": None}, sole, PACKS).status == "many"
    assert resolve_payee({"text": "Dr Chen", "relation": None}, sole, PACKS).status == "one"
    assert resolve_payee({"text": "Chen", "relation": None}, sole, PACKS).status == "one"
