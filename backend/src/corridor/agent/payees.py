"""Deterministic payee resolution (no LLM), docs/spec/modules.md 5.15.

``payee_ref`` from the NLU (a word the user typed plus a normalised relation) is matched only against
the payees returned by ``get_payees`` for the authenticated user. A payee id from model output is never
accepted (INV-4). Zero matches means ask who it is, several means ask which one, exactly one means use
the stored payee, which carries ``Provenance.SYSTEM`` because it comes from the bank's own records.
"""

from dataclasses import dataclass
from typing import Any

from corridor.nlu.lang_pack import LangPacks

# a stored relation that also covers these asked-for relations
_COVERS = {"parents": {"mother", "father", "parents"}}


@dataclass(frozen=True)
class Resolution:
    """Outcome of resolving a payee reference."""

    status: str  # one | none | many
    matches: tuple[dict[str, Any], ...]

    @property
    def payee(self) -> dict[str, Any] | None:
        return self.matches[0] if self.status == "one" else None


def _all_tokens(display_name: str, packs: LangPacks) -> set[str]:
    base = display_name.split("(")[0]  # "Ama (mother, fictional)" -> the name part
    return {packs.fold(w.strip(".,")) for w in base.split() if w.strip(".,")}


def _name_tokens(display_name: str, packs: LangPacks) -> set[str]:
    return _all_tokens(display_name, packs) - packs.name_noise


def resolve_payee(
    ref: dict[str, Any], payees: list[dict[str, Any]], packs: LangPacks
) -> Resolution:
    """Resolve ``ref`` ({"text": str, "relation": str | None}) against ``payees`` (the user's own)."""
    words = {packs.fold(w.strip(".,")) for w in str(ref.get("text", "")).split() if w.strip(".,")}
    # a title or respectful suffix ("Mr", "dai", "sir") decorates a name; it never identifies a payee
    typed_noise = words & packs.name_noise
    words = (words - packs.name_noise) or words
    relation = ref.get("relation")
    found: dict[str, dict[str, Any]] = {}
    for p in payees:
        stored_rel = p.get("relation")
        by_relation = bool(
            relation
            and stored_rel
            and (stored_rel == relation or relation in _COVERS.get(stored_rel, set()))
        )
        by_name = bool(words) and words <= _name_tokens(
            str(p.get("match_name") or p.get("display_name", "")), packs
        )  # every typed word must be in the name
        if by_relation or by_name:
            found[str(p["payee_id"])] = p
    matches = tuple(found.values())
    status = "none" if not matches else ("one" if len(matches) == 1 else "many")
    if status == "one" and typed_noise:
        # the user wrote a title or respectful suffix ("Mr", "didi") and the stored name carries a different
        # one ("Dr", "dai"): two people can share a first name, so ask instead of picking (P2-6)
        stored_noise = (
            _all_tokens(
                str(matches[0].get("match_name") or matches[0].get("display_name", "")), packs
            )
            & packs.name_noise
        )
        if stored_noise and stored_noise != typed_noise:
            status = "many"
    return Resolution(status, matches)
