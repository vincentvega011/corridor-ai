"""Provenance helpers (docs/spec/modules.md 5.6, INV-4).

Trust only goes down through computation: ``derive`` takes the lowest provenance of its inputs, and
a confirmed input counts as merely user typed, so confirmation never propagates to a computed value.
Nothing in this package can raise trust and nothing here creates ``USER_CONFIRMED``: only
``corridor.confirm`` may do that, and only for the exact value the user was shown.
"""

from typing import Any

from corridor.contracts.provenance import (
    TRUST_ORDER,
    Provenance,
    Tainted,
    derive,
    lowest_trust,
    trust_rank,
)

__all__ = [
    "TRUST_ORDER",
    "Provenance",
    "Tainted",
    "derive",
    "derive_from_model",
    "is_tainted",
    "lowest_trust",
    "trust_rank",
]

_TAINTED = frozenset({Provenance.UNTRUSTED, Provenance.MODEL_INFERRED})


def is_tainted(provenance: Provenance) -> bool:
    """True for untrusted or model inferred provenance: never treated as typed or confirmed."""
    return provenance in _TAINTED


def derive_from_model(value: Any, *inputs: "Tainted[Any] | Provenance") -> Tainted[Any]:
    """Wrap a value an LLM produced from ``inputs``: never more trusted than ``MODEL_INFERRED``."""
    return derive(value, Provenance.MODEL_INFERRED, *inputs)
