"""Keyword intent classifier: a deterministic cross check for the model (never the only signal).

Keywords come from ``data/lang/<code>/intents.yaml``. Each matched keyword scores its length in
tokens, so multi word phrases outweigh single words. The classifier abstains (returns ``None``)
on no match or a tie, and the confidence formula treats an abstention as neutral, a disagreement
with the model as a hard failure (see ``confidence.py`` and ADR 0004).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from corridor.contracts.intent import IntentType
from corridor.nlu.lang_pack import LangPacks, load_packs
from corridor.nlu.normalise import Normalised, _match_seq, _tokenise, normalise


@dataclass(frozen=True)
class IntentGuess:
    """The classifier result: ``intent`` is None when it abstains."""

    intent: IntentType | None
    scores: dict[str, int]


def classify_intent(
    text: str,
    norm: Normalised | None = None,
    packs: LangPacks | None = None,
    today: date | None = None,
) -> IntentGuess:
    """Classify ``text`` by keywords. ``norm`` is used for recurrence evidence."""
    packs = packs or load_packs()
    toks = _tokenise(text, packs)
    for t in toks:  # common misspellings are corrected for the keyword match only
        t.folded = packs.misspellings.get(t.folded, t.folded)
    norm = norm or normalise(text, today or date(2000, 1, 1), packs)
    scores: dict[str, int] = {}
    for intent, phrases in packs.intent_keywords.items():
        score = 0
        for seq in phrases:
            for i in range(len(toks)):
                if _match_seq(toks, i, seq):
                    score += len(seq)
        if score:
            scores[intent] = score
    if "schedule_transfer" in scores:
        needs = packs.intent_requires.get("schedule_transfer", ())
        if not any(n in scores for n in needs) and not norm.amounts:
            del scores["schedule_transfer"]
    if "schedule_transfer" in scores and norm.recurrences:
        scores["schedule_transfer"] += 1
    if not scores:
        return IntentGuess(None, {})
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return IntentGuess(None, scores)
    return IntentGuess(IntentType(ranked[0][0]), scores)


def compatible(model_intent: IntentType, guess: IntentGuess, norm: Normalised) -> bool:
    """Whether the model's intent agrees with the classifier (schedule vs send is compatible when
    the text has a recurrence)."""
    if guess.intent is None or model_intent is guess.intent:
        return True
    pair = {model_intent, guess.intent}
    return bool(pair == {IntentType.SCHEDULE_TRANSFER, IntentType.SEND_MONEY} and norm.recurrences)


__all__: Sequence[str] = ["IntentGuess", "classify_intent", "compatible"]


def is_attachment_imperative(text: str, packs: LangPacks | None = None) -> bool:
    """True when the WHOLE message is a short imperative such as "pay this" or "yo tirdeu".

    With an attached document that means "pay the attached invoice". It is decided by data driven
    code on the user's own words, never by a model, and it only ever selects an intent: the amount,
    the beneficiary and the account still come from the stored invoice (INV-3, INV-4)."""
    packs = packs or load_packs()
    toks = tuple(
        t.folded
        for t in _tokenise(text, packs)
        if t.folded and t.folded not in packs.imperative_fillers
    )
    return bool(toks) and toks in packs.attachment_imperatives
