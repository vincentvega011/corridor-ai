"""Typed outputs of the quarantined reader (docs/spec/modules.md 5.5).

Security reason (INV-3, INV-4): documents, images and messages are untrusted. The reader
returns only these schemas. A validator on every field rewrites provenance to UNTRUSTED
no matter what the producer claimed, so no code path can smuggle a SYSTEM or USER_TYPED
label in through a document. Free text is capped at 120 characters and is data, never an
instruction. Instruction-like content raises ``injection_suspected`` which the policy
engine and risk model consume. The two flags are plain booleans: they can only add
caution (a fooled reader that reports False simply fails to add it; it cannot remove a
protection that other signals still apply).
"""

from datetime import date
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from corridor.contracts.money import Money
from corridor.contracts.provenance import Provenance, Tainted
from corridor.contracts.scalars import Sha256Hex, ShortText


def _force_untrusted(value: Any) -> Any:
    if isinstance(value, Tainted):
        return Tainted[Any](
            value=value.value, provenance=Provenance.UNTRUSTED, source_ref=value.source_ref
        )
    if isinstance(value, tuple | list):
        return tuple(_force_untrusted(v) for v in value)
    return value


class _UntrustedDoc(BaseModel):
    """Base for reader outputs: forces every Tainted field to UNTRUSTED."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_doc_hash: Sha256Hex
    injection_suspected: bool = False
    contains_payment_instructions: bool = False

    @field_validator("*", mode="after")
    @classmethod
    def _untrusted(cls, value: Any) -> Any:
        return _force_untrusted(value)

    @model_validator(mode="after")
    def _all_untrusted(self) -> Self:
        # Defence in depth: re-check after construction in case a subclass adds a field
        # the generic validator could not see.
        for name in type(self).model_fields:
            v = getattr(self, name)
            items = v if isinstance(v, tuple) else (v,)
            for item in items:
                if isinstance(item, Tainted) and item.provenance is not Provenance.UNTRUSTED:
                    raise ValueError(f"field {name} is not UNTRUSTED")
        return self


class InvoiceDoc(_UntrustedDoc):
    """Fields read from an invoice. The beneficiary account is what an attacker would swap."""

    issuer: Tainted[ShortText]
    amount: Tainted[Money] | None = None
    due_date: Tainted[date] | None = None
    beneficiary_name: Tainted[ShortText] | None = None
    beneficiary_account: Tainted[ShortText] | None = None
    reference: Tainted[ShortText] | None = None


class MessageDoc(_UntrustedDoc):
    """Fields read from a pasted SMS, email or chat screenshot."""

    sender_label: Tainted[ShortText]
    claimed_relationship: Tainted[ShortText] | None = None
    requested_amount: Tainted[Money] | None = None
    mentioned_account: Tainted[ShortText] | None = None
    urgency_markers: tuple[Tainted[ShortText], ...] = Field(default=(), max_length=10)


class StatementDoc(_UntrustedDoc):
    """Fields read from a bank or provider statement."""

    issuer: Tainted[ShortText]
    period_start: Tainted[date] | None = None
    period_end: Tainted[date] | None = None
    closing_balance: Tainted[Money] | None = None
    entry_count: Tainted[int] | None = None
