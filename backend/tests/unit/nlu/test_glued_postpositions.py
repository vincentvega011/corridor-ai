"""Postpositions written glued to a time word are split by data, never by guess (P4-0)."""

from __future__ import annotations

from datetime import date

import pytest

from corridor.nlu.lang_pack import load_packs
from corridor.nlu.normalise import normalise

TODAY = date(2026, 10, 10)
PACKS = load_packs()


def view(text: str) -> tuple[list[tuple[date | None, bool]], list[tuple[str, date | None, str]]]:
    n = normalise(text, TODAY, PACKS)
    return (
        [(d.date, d.is_past) for d in n.dates],
        [(f.festival_id, f.date, f.deadline_kind) for f in n.festivals],
    )


GLUED_VS_SPACED = [
    ("Ama lai 20 hajar dashainaghi pathau", "Ama lai 20 hajar dashain aghi pathau"),
    ("Ama lai 20 hajar dashainagadi pathau", "Ama lai 20 hajar dashain agadi pathau"),
    ("Ama lai 5 hajar tiharbhitra pathau", "Ama lai 5 hajar tihar bhitra pathau"),
    ("Ama lai 5 hajar tiharsamma pathau", "Ama lai 5 hajar tihar samma pathau"),
    ("Ama lai 5 hajar bholisamma pathau", "Ama lai 5 hajar bholi samma pathau"),
    ("Ama lai 8 hajar hijosamma pathau", "Ama lai 8 hajar hijo samma pathau"),
    ("आमालाई ८ हजार हिजोसम्म पठाउ", "आमालाई ८ हजार हिजो सम्म पठाउ"),
    ("दशैंअघि आमालाई २० हजार पठाउ", "दशैं अघि आमालाई २० हजार पठाउ"),
    ("दशैअगाडि आमालाई २० हजार पठाउ", "दशै अगाडि आमालाई २० हजार पठाउ"),
    ("आमालाई ५ हजार तिहारभित्र पठाउ", "आमालाई ५ हजार तिहार भित्र पठाउ"),
    ("आमालाई ५ हजार भोलिसम्म पठाउ", "आमालाई ५ हजार भोलि सम्म पठाउ"),
]


@pytest.mark.parametrize(("glued", "spaced"), GLUED_VS_SPACED)
def test_a_glued_postposition_reads_like_the_spaced_one(glued: str, spaced: str) -> None:
    assert view(glued) == view(spaced)
    assert view(glued) != ([], [])  # and it is found at all


@pytest.mark.parametrize(
    "text",
    [
        "Ama lai 20 hajar dashainpachhi pathau",
        "आमालाई २० हजार तिहारपछि पठाउ",
        "send 20k to Ama after Dashain",
        "Ama lai 20 hajar dashain pachhi pathau",
    ],
)
def test_after_a_festival_is_not_a_deadline(text: str) -> None:
    assert view(text) == ([], [])


@pytest.mark.parametrize(
    "word",
    [
        "Dashain",
        "dasai",
        "Tihar",
        "Ghatasthapana",
        "ghatasthapana",
        "Chhath",
        "diwali",
        "दशैं",
        "तिहार",
        "घटस्थापना",
        "छठ",
    ],
)
def test_festival_names_are_never_cut(word: str) -> None:
    from corridor.nlu.normalise import _tokenise

    toks = _tokenise(f"pathau {word} lai", PACKS)
    assert [t.text for t in toks if t.text.lower() == word.lower()] == [word.lower()] or any(
        t.text == word for t in toks
    )
    assert len(toks) == 3  # nothing was split


@pytest.mark.parametrize(
    "text",
    [
        "Aghi Sharma lai 5 hajar pathau",
        "Sammanita lai 5 hajar pathau",
        "Pachhima lai 5 hajar pathau",
        "Bhitrakot lai 5 hajar pathau",
    ],
)
def test_names_that_look_like_a_postposition_are_not_split(text: str) -> None:
    from corridor.nlu.normalise import _tokenise

    assert len(_tokenise(text, PACKS)) == len(text.split())
