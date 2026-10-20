"""How a user answers "that date has already passed" (P3-4c). Pure rules, no model.

The question offers a new date, or "send now" (continue with no deadline). A reply is one of:
``cancel`` (a negation: "no", "cancel", "hoina", "रद्द गर"), ``affirm`` (any clear go-ahead without a
date), or ``None`` (unrelated: the question is asked again). A date in the reply is handled by the
parser as a new deadline, not here. Negations that belong to an affirmation ("no deadline", "without
a deadline") count as ``affirm``.
"""

from __future__ import annotations

import re

_PHRASE_AFFIRM = (
    "no deadline",
    "without a deadline",
    "without deadline",
    "miti nabhani",
    "मिति नभनी",
)
_CANCEL = frozenset(
    [
        "no",
        "nope",
        "cancel",
        "stop",
        "dont",
        "never",
        "nevermind",
        "hoina",
        "haina",
        "nahi",
        "radd",
        "nagar",
        "napathau",
        "nachahine",
        "chodnu",
        "रद्द",
        "होइन",
        "हैन",
        "नगर",
        "नपठाऊ",
        "नपठाउ",
        "छोड",
        "छोडिदेऊ",
    ]
)
_AFFIRM = frozenset(
    [
        "yes",
        "yeah",
        "ok",
        "okay",
        "now",
        "send",
        "proceed",
        "continue",
        "go",
        "ahead",
        "sure",
        "haan",
        "ho",
        "hunchha",
        "huncha",
        "thik",
        "ahile",
        "aile",
        "pathau",
        "pathaideu",
        "jari",
        "हो",
        "हुन्छ",
        "ठिक",
        "अहिले",
        "पठाऊ",
        "पठाउ",
        "जारी",
        "हस्",
    ]
)


def _tokens(text: str) -> list[str]:
    text = text.lower().replace("'", "")
    return [t for t in re.split(r"[^\wऀ-ॿ]+", text) if t]


def classify(text: str) -> str | None:
    """``cancel``, ``affirm`` or ``None`` (unrelated, ask again)."""
    low = text.lower()
    if any(p in low for p in _PHRASE_AFFIRM):
        return "affirm"
    toks = _tokens(text)
    if any(t in _CANCEL for t in toks):
        return "cancel"
    if any(t in _AFFIRM for t in toks):
        return "affirm"
    return None
