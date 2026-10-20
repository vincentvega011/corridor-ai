"""Schemas an LLM is allowed to return. This is a security boundary.

Every model here holds PLAIN values and has NO provenance, trust or source field, and all
forbid extra fields. Consequences:

- INV-4 (taint is sticky, no LLM can remove it): a model has nowhere to write "this value is
  trusted". Provenance is attached by our own code when it converts a draft into a
  contract (``IntentFrame``, ``PlanStep``, ``ActionProposal``, documents). The parser's
  values become USER_TYPED or MODEL_INFERRED, the reader's become UNTRUSTED, by code.
- INV-3 (untrusted content never reaches the planner as text): the reader returns typed
  fields with capped length, not prose; the planner returns structure, not free text.
- INV-1 (the LLM never executes): ``ProposalDraft`` omits user_id, idempotency_key, risk and
  route. Those are supplied by trusted code, so a model cannot choose whose money moves,
  cannot pick or reuse an idempotency key, and cannot invent a route.

The extra="forbid" setting means a payload that includes a ``provenance`` key is rejected
outright, and argument dictionaries reject keys that look like trust metadata.
"""

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from corridor.contracts.action import ActionType
from corridor.contracts.intent import IntentType, LanguageTag, SlotName
from corridor.contracts.money import CurrencyCode, MinorUnits
from corridor.contracts.plan import MAX_PLAN_STEPS, StepKind
from corridor.contracts.scalars import Bps, IdStr, ShortText

PlainStr = Annotated[StrictStr, StringConstraints(max_length=120)]
PositiveMinor = Annotated[MinorUnits, Field(gt=0, le=10**15)]
"""Strictly positive, bounded amount for LLM-facing schemas."""

PlainScalar = PlainStr | StrictInt | StrictBool | None
"""JSON scalars an LLM may emit as slot or argument values. No floats: amounts are ints."""

_FORBIDDEN_KEYS = frozenset({"provenance", "source_ref", "trust", "tainted"})


class _LLMModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ParserOutput(_LLMModel):
    """What the NLU parser model returns for a user utterance."""

    language: LanguageTag
    intent: IntentType
    slots: dict[SlotName, PlainScalar] = Field(default_factory=dict)
    missing_slots: tuple[SlotName, ...] = ()
    confidence_bps: Bps


class PlannerStepDraft(_LLMModel):
    """One planned step as the planner proposes it (no provenance on arguments)."""

    id: IdStr
    kind: StepKind
    tool: IdStr
    args: dict[str, PlainScalar | tuple[PlainScalar, ...]] = Field(default_factory=dict)
    depends_on: tuple[IdStr, ...] = ()

    @field_validator("args")
    @classmethod
    def _no_trust_keys(
        cls, v: dict[str, PlainScalar | tuple[PlainScalar, ...]]
    ) -> dict[str, PlainScalar | tuple[PlainScalar, ...]]:
        if len(v) > 16:
            raise ValueError("a step may have at most 16 arguments")
        bad = _FORBIDDEN_KEYS.intersection(k.lower() for k in v)
        if bad:
            raise ValueError(
                f"argument names {sorted(bad)} are reserved and cannot come from a model"
            )
        return v


class ProposalDraft(_LLMModel):
    """An action the planner wants proposed, by reference. Trusted code fills in the rest.

    ``source_account_ref`` and ``payee_ref`` are references the code resolves against system
    data; the amount is integer minor units.
    """

    action: ActionType
    source_account_ref: IdStr
    payee_ref: IdStr | None = None
    amount_minor: PositiveMinor | None = None
    currency: CurrencyCode | None = None
    invoice_ref: IdStr | None = None
    rationale: ShortText = ""
    """Model authored and unsigned: never show it on a confirmation card as a signed field."""

    @model_validator(mode="after")
    def _amount_pair(self) -> Self:
        if (self.amount_minor is None) != (self.currency is None):
            raise ValueError("amount_minor and currency must be given together")
        if self.invoice_ref is not None and (
            self.amount_minor is not None or self.payee_ref is not None
        ):
            # The amount and payee of an invoice payment come from the stored invoice, with
            # its UNTRUSTED provenance. A model must not re-state them, or the taint is lost.
            raise ValueError(
                "invoice payments take amount and payee from the invoice, not the model"
            )
        return self


class PlannerOutput(_LLMModel):
    """The planner's output: READ steps plus at most one proposal draft.

    Structural validation happens when converted to ``Plan``; the draft is only a request, and every
    value that matters in it is re-derived or checked by trusted code (INV-1, INV-4)."""

    steps: tuple[PlannerStepDraft, ...] = Field(max_length=MAX_PLAN_STEPS)
    proposal: ProposalDraft | None = None


class ComposerOutput(_LLMModel):
    """The composer's reply text. Plain text only, no provenance, capped in length."""

    reply: str = Field(min_length=1, max_length=600)


class InvoiceFields(_LLMModel):
    """Plain invoice fields as the reader model returns them."""

    issuer: ShortText
    amount_minor: PositiveMinor | None = None
    currency: CurrencyCode | None = None
    due_date: date | None = None
    beneficiary_name: ShortText | None = None
    beneficiary_account: ShortText | None = None
    reference: ShortText | None = None

    @model_validator(mode="after")
    def _pair(self) -> Self:
        if (self.amount_minor is None) != (self.currency is None):
            raise ValueError("amount_minor and currency must be given together")
        return self


class MessageFields(_LLMModel):
    """Plain message fields as the reader model returns them."""

    sender_label: ShortText
    claimed_relationship: ShortText | None = None
    requested_amount_minor: PositiveMinor | None = None
    requested_currency: CurrencyCode | None = None
    mentioned_account: ShortText | None = None
    urgency_markers: tuple[ShortText, ...] = Field(default=(), max_length=10)

    @model_validator(mode="after")
    def _pair(self) -> Self:
        if (self.requested_amount_minor is None) != (self.requested_currency is None):
            raise ValueError("requested amount and currency must be given together")
        return self


class StatementFields(_LLMModel):
    """Plain statement fields as the reader model returns them."""

    issuer: ShortText
    period_start: date | None = None
    period_end: date | None = None
    closing_balance_minor: Annotated[MinorUnits, Field(le=10**15, ge=-(10**15))] | None = None
    closing_currency: CurrencyCode | None = None
    entry_count: StrictInt | None = None

    @model_validator(mode="after")
    def _pair(self) -> Self:
        if (self.closing_balance_minor is None) != (self.closing_currency is None):
            raise ValueError("closing balance and currency must be given together")
        return self


class ReaderOutput(_LLMModel):
    """The quarantined reader's output: exactly one typed document plus the two safety flags."""

    doc_type: Literal["invoice", "message", "statement"]
    invoice: InvoiceFields | None = None
    message: MessageFields | None = None
    statement: StatementFields | None = None
    contains_payment_instructions: StrictBool = False
    injection_suspected: StrictBool = False
    notable_text: tuple[ShortText, ...] = Field(default=(), max_length=10)
    """Verbatim text that reads like an instruction, a note to an AI or a payment redirection.
    Scanned by deterministic patterns and stored with the document, never shown to the planner."""

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        present = {
            "invoice": self.invoice is not None,
            "message": self.message is not None,
            "statement": self.statement is not None,
        }
        if sum(present.values()) != 1 or not present[self.doc_type]:
            raise ValueError("reader output must contain exactly the document matching doc_type")
        return self
