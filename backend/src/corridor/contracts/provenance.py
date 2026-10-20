"""Provenance labels and the Tainted wrapper (docs/spec/modules.md 5.6).

Security reason (INV-4): every value carries where it came from. Trust can only go down
through computation. This module deliberately contains NO function that raises trust:
upgrading a value to ``USER_CONFIRMED`` belongs to ``corridor.confirm`` and only for the
exact value the user was shown. Because LLM output schemas (``llm_outputs``) have no
provenance field, a model cannot assert that anything is trusted.

Limitation: Python cannot stop trusted code from building a ``Tainted`` with any
provenance (or using ``model_copy(update=...)``). The guarantee is structural: no LLM
facing schema carries provenance, and review plus tests guard the few construction sites.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class Provenance(StrEnum):
    """Where a value came from."""

    USER_TYPED = "user_typed"  # the user's own words in this session
    USER_CONFIRMED = "user_confirmed"  # user explicitly confirmed this exact value
    SYSTEM = "system"  # from the sandbox DB or a trusted tool
    UNTRUSTED = "untrusted"  # from any document, image, message, third party
    MODEL_INFERRED = "model_inferred"  # filled in by an LLM, not stated by anyone


TRUST_ORDER: tuple[Provenance, ...] = (
    Provenance.UNTRUSTED,
    Provenance.MODEL_INFERRED,
    Provenance.USER_TYPED,
    Provenance.SYSTEM,
)
"""Ascending trust. ``USER_CONFIRMED`` is not in this chain: it is a status granted by the
confirmation flow to one exact value, not a level a computation can reach."""

_RANK: dict[Provenance, int] = {p: i for i, p in enumerate(TRUST_ORDER)}
# For derivation only, a confirmed input counts as user typed. A value computed from a
# confirmed value is a new value the user did not confirm, so confirmation never propagates.
_RANK[Provenance.USER_CONFIRMED] = _RANK[Provenance.USER_TYPED]


def trust_rank(provenance: Provenance) -> int:
    """Return the derivation rank of ``provenance`` (higher is more trusted)."""
    return _RANK[provenance]


class Tainted[T](BaseModel):
    """A value together with its provenance and a reference to its origin.

    ``source_ref`` is a tool call id or document hash so a reviewer can trace the value.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: T
    provenance: Provenance
    source_ref: str | None = None


def lowest_trust(*values: "Tainted[Any] | Provenance") -> Provenance:
    """Return the lowest provenance among ``values`` (the propagation rule).

    The result is never higher than any input, and never ``USER_CONFIRMED`` (see
    ``TRUST_ORDER``). Raises ``ValueError`` with no inputs: a derived value with no
    inputs has no defensible trust level (INV-10, fail closed).
    """
    if not values:
        raise ValueError("lowest_trust requires at least one input")
    provs = [v.provenance if isinstance(v, Tainted) else v for v in values]
    lowest = min(provs, key=trust_rank)
    if lowest is Provenance.USER_CONFIRMED:
        return Provenance.USER_TYPED
    return lowest


def derive(
    value: Any, *inputs: "Tainted[Any] | Provenance", source_ref: str | None = None
) -> "Tainted[Any]":
    """Wrap a computed ``value`` with the lowest trust of the inputs it was computed from."""
    return Tainted[Any](value=value, provenance=lowest_trust(*inputs), source_ref=source_ref)
