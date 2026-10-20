"""A payee the user types in chat: where its account number and name come from (P3-2, ADR 0018).

Origin is decided by code, never by a model (INV-3, INV-4):

* ``USER_TYPED``: the digits appear verbatim in the user's own typed words.
* ``UNTRUSTED``: the digits appear only in pasted or forwarded content (or the split of the user's words
  from pasted content was uncertain). Pasted content stays untrusted.
* ``MODEL_INFERRED``: a model supplied digits that appear in neither. Nothing in the shipped agent does
  this (the parser never sees a full number); the function exists so the rule is explicit and tested.

The full number never leaves this module in clear: the caller gets its keyed hash and last four digits.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from corridor.contracts.provenance import Provenance, trust_rank

_DEVA = str.maketrans("०१२३४५६७८९", "0123456789")
# 6 to 20 digits, optionally in groups separated by a space or hyphen (062-000-771234, 0620 0077 1234)
_RUN = re.compile(r"(?<![\d])\d(?:[ -]?\d){5,19}(?![\d])")
_LEAD = re.compile(
    r"^(please|pls|add|save|create|register|my|a|an|the|new|payee|landlord|recipient|beneficiary|to|as)\b",
    re.I,
)
_TAIL = re.compile(
    r"\b(account|acct|a/c|ac|no|number|num|bsb|iban|payee|landlord|is|at|with|for)\b.*$", re.I
)
_STOP = frozenset({"him", "her", "them", "it", "this", "that", "someone", "somebody", "payee"})
REGION_OF = {"AUD": "AU", "NPR": "NP", "QAR": "QA", "CNY": "CN", "MYR": "MY", "USD": "US"}


@dataclass(frozen=True)
class AccountRef:
    """An account number found in a message: keyed hash and last four, plus where it came from."""

    account_hash: str
    last4: str
    provenance: Provenance


def digit_runs(text: str) -> list[str]:
    """Every run of 6 to 20 digits in ``text`` (ASCII or Devanagari), separators removed."""
    flat = text.translate(_DEVA)
    return [re.sub(r"\D", "", m.group(0)) for m in _RUN.finditer(flat)]


def find_account(
    typed: str,
    pasted: str,
    *,
    uncertain_split: bool,
    hash_fn: Callable[[str], str],
    model_digits: str | None = None,
) -> AccountRef | None:
    """The account number the user means and how far to trust it, or None if there is none.

    Exactly one distinct number must be found: two different numbers are ambiguous and the caller asks."""
    in_typed = set() if uncertain_split else set(digit_runs(typed))
    in_pasted = set(digit_runs(pasted)) | (set(digit_runs(typed)) if uncertain_split else set())
    if len(in_typed | in_pasted) > 1:
        return None
    if in_typed:
        digits, prov = next(iter(in_typed)), Provenance.USER_TYPED
    elif in_pasted:
        digits, prov = next(iter(in_pasted)), Provenance.UNTRUSTED
    elif model_digits and re.fullmatch(r"\d{6,20}", model_digits):
        digits, prov = model_digits, Provenance.MODEL_INFERRED
    else:
        return None
    return AccountRef(hash_fn(digits), digits[-4:], prov)


def find_name(typed: str, slot_text: str | None = None) -> tuple[str, Provenance] | None:
    """The payee's name from the user's own words: the text around the number, cleaned of filler words.

    A name that appears verbatim in the typed text is ``USER_TYPED``. A name only a model supplied
    (``slot_text`` not found in the typed text) is ``MODEL_INFERRED``."""
    flat = typed.translate(_DEVA)
    head = _RUN.split(flat, maxsplit=1)[0]
    words = head.replace(",", " ").split()
    while words and _LEAD.match(words[0]):
        words.pop(0)
    name = _TAIL.sub("", " ".join(words)).strip(" .:-")
    if len(name) >= 2 and any(c.isalpha() for c in name) and name.lower() not in _STOP:
        return name[:80], Provenance.USER_TYPED
    if slot_text and slot_text.strip():
        s = slot_text.strip()[:80]
        return s, Provenance.USER_TYPED if s.lower() in typed.lower() else Provenance.MODEL_INFERRED
    return None


def least_trusted(*provs: Provenance) -> Provenance:
    """The lowest trust among ``provs`` (what the saved payee row records)."""
    return min(provs, key=trust_rank)
