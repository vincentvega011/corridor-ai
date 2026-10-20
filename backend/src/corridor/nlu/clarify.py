"""Clarification questions from templates (no LLM): data/lang/*/clarify.yaml.

One short question per missing or ambiguous slot, in the user's language style (en, ne, ne_latn).
``mixed`` uses the romanised Nepali templates because mixed messages are Latin script by default
(Devanagari plus Latin mixes use the Devanagari ones).
"""

from pydantic import BaseModel, ConfigDict

from corridor.contracts.intent import LanguageTag
from corridor.nlu.lang_pack import LangPacks, load_packs

STYLES = ("en", "ne", "ne_latn")


class ClarifyRequest(BaseModel):
    """What to ask: missing slot names, ambiguous options, and a ready question text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    missing_slots: tuple[str, ...]
    ambiguous: dict[str, tuple[str, ...]] = {}
    style: str
    question: str


def style_for(language: LanguageTag, deva: bool = False) -> str:
    """Template style for a detected language."""
    if language is LanguageTag.EN:
        return "en"
    if language is LanguageTag.NE or (language is LanguageTag.MIXED and deva):
        return "ne"
    return "ne_latn"


def question_for(slot: str, style: str, packs: LangPacks, options: tuple[str, ...] = ()) -> str:
    """The template question for ``slot`` ({options} filled in). Raises KeyError if no template."""
    key = f"{slot}_ambiguous" if options and f"{slot}_ambiguous" in packs.clarify else slot
    text = packs.clarify[key][style]
    return text.replace("{options}", " / ".join(options))


def build_clarify(
    missing: tuple[str, ...],
    ambiguous: dict[str, tuple[str, ...]],
    language: LanguageTag,
    deva: bool,
    packs: LangPacks | None = None,
) -> ClarifyRequest | None:
    """Return a clarification request, or None if nothing is missing or ambiguous."""
    packs = packs or load_packs()
    if not missing and not ambiguous:
        return None
    style = style_for(language, deva)
    parts = [question_for(s, style, packs, ambiguous.get(s, ())) for s in ambiguous]
    parts += [question_for(s, style, packs) for s in missing if s not in ambiguous]
    return ClarifyRequest(
        missing_slots=tuple(dict.fromkeys([*missing, *ambiguous])),
        ambiguous=ambiguous,
        style=style,
        question=" ".join(parts[:2]),  # at most two questions at a time
    )


def generic_question(
    language: LanguageTag, deva: bool, packs: LangPacks | None = None, blocked: bool = False
) -> ClarifyRequest:
    """A fixed "please rephrase" (or "do not type secrets") request when parsing failed closed."""
    packs = packs or load_packs()
    style = style_for(language, deva)
    key = "blocked_content" if blocked else "generic"
    return ClarifyRequest(missing_slots=(), style=style, question=packs.clarify[key][style])
