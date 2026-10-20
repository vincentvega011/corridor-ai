"""ActionProposal: the only thing an LLM-driven planner can emit (INV-1).

A proposal is a request. Nothing happens until policy allows it, the user confirms the
exact hash (INV-5) and the executor runs it (INV-6).
"""

from datetime import date
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, model_validator

from corridor.contracts.canonical import sha256_hex
from corridor.contracts.money import CurrencyCode, Money
from corridor.contracts.provenance import Tainted
from corridor.contracts.risk import RiskAssessment
from corridor.contracts.routing import RouteRecommendation
from corridor.contracts.scalars import IdStr, Sha256Hex, ShortText

HASH_SCHEMA_VERSION = 2


class ActionType(StrEnum):
    """Every action the system can propose. Unknown actions are denied by policy (INV-10)."""

    TRANSFER = "transfer"
    PAY_INVOICE = "pay_invoice"
    CONVERT = "convert"
    SCHEDULE = "schedule"
    CANCEL_SCHEDULE = "cancel_schedule"
    ADD_PAYEE = "add_payee"
    ISSUE_PASSPORT = "issue_passport"
    SET_BUDGET = "set_budget"
    REQUEST_CONTRIBUTION = "request_contribution"
    SHARE_PASSPORT = "share_passport"


def _values(spec: BaseModel | None) -> dict[str, Any] | None:
    """Return a spec's values with provenance stripped (provenance is never hashed)."""
    if spec is None:
        return None
    out: dict[str, Any] = {}
    for name in type(spec).model_fields:
        v = getattr(spec, name)
        out[name] = v.value if isinstance(v, Tainted) else v
    return out


class NewPayeeSpec(BaseModel):
    """Terms of an ADD_PAYEE action. Every value is Tainted: a payee built from a document
    keeps UNTRUSTED provenance, which R-PAYEE-ADD and R-TAINT-PAYEE read (INV-4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    display_name: Tainted[ShortText]
    region: Tainted[ShortText]
    currency: CurrencyCode
    account_no: Tainted[ShortText]


class ScheduleSpec(BaseModel):
    """Terms of a SCHEDULE action: the recurrence rule and first run date the user confirms."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rrule: Tainted[ShortText]
    first_run: Tainted[date]


class ContributionSpec(BaseModel):
    """Terms of a REQUEST_CONTRIBUTION action (P3-1, ADR 0014): ask a linked family member.

    ``amount`` of the proposal is what is asked, in the contributor's currency;
    ``target_account_id``
    is the requester's own account that would receive it. The request moves no money (INV-1): the
    contributor decides in their own account."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contributor_user_id: IdStr
    target_account_id: IdStr


class PassportShareSpec(BaseModel):
    """Terms of a SHARE_PASSPORT action (P3-1, ADR 0015): which claims of which passport go to whom.

    The user confirms exactly these claims (WYSIWYS). Everything else stays hidden: the presentation
    contains only these disclosures."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    passport_id: IdStr
    claim_ids: tuple[IdStr, ...]
    audience: ShortText
    nonce: ShortText | None = None
    """The verifier's challenge this presentation answers (None: the system issues a holder
    challenge)."""

    @model_validator(mode="after")
    def _claims(self) -> Self:
        if not 1 <= len(self.claim_ids) <= 8 or len(set(self.claim_ids)) != len(self.claim_ids):
            raise ValueError("share between 1 and 8 distinct claims")
        return self


class BudgetSpec(BaseModel):
    """Terms of a SET_BUDGET action."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: ShortText
    limit: Money


class ActionProposal(BaseModel):
    """A proposed state change with provenance on every value that matters.

    ``canonical_hash`` is what the user's confirmation is bound to.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_id: IdStr
    action: ActionType
    user_id: IdStr
    source_account_id: Tainted[str]
    payee_id: Tainted[str] | None = None
    amount: Tainted[Money] | None = None
    invoice_id: IdStr | None = None
    target_currency: CurrencyCode | None = None  # CONVERT
    new_payee: NewPayeeSpec | None = None  # ADD_PAYEE
    schedule: ScheduleSpec | None = None  # SCHEDULE
    schedule_id: IdStr | None = None  # CANCEL_SCHEDULE
    budget: BudgetSpec | None = None  # SET_BUDGET
    contribution: ContributionSpec | None = None  # REQUEST_CONTRIBUTION
    passport_share: PassportShareSpec | None = None  # SHARE_PASSPORT
    route: RouteRecommendation | None = None
    risk: RiskAssessment | None = None
    derived_from_docs: tuple[Sha256Hex, ...] = ()
    idempotency_key: IdStr

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.amount is not None and self.amount.value.amount_minor <= 0:
            raise ValueError("proposal amount must be positive")
        needs_amount = (
            ActionType.TRANSFER, ActionType.PAY_INVOICE, ActionType.CONVERT,
            ActionType.REQUEST_CONTRIBUTION,
        )  # fmt: skip
        if self.action in needs_amount and self.amount is None:
            raise ValueError(f"{self.action.value} requires an amount")
        needs_payee = (ActionType.TRANSFER, ActionType.PAY_INVOICE)
        if self.action in needs_payee and self.payee_id is None:
            raise ValueError(f"{self.action.value} requires a payee")
        if self.action is ActionType.PAY_INVOICE and self.invoice_id is None:
            raise ValueError("pay_invoice requires an invoice_id")
        required: dict[ActionType, tuple[str, object]] = {
            ActionType.CONVERT: ("target_currency", self.target_currency),
            ActionType.ADD_PAYEE: ("new_payee", self.new_payee),
            ActionType.SCHEDULE: ("schedule", self.schedule),
            ActionType.CANCEL_SCHEDULE: ("schedule_id", self.schedule_id),
            ActionType.SET_BUDGET: ("budget", self.budget),
            ActionType.REQUEST_CONTRIBUTION: ("contribution", self.contribution),
            ActionType.SHARE_PASSPORT: ("passport_share", self.passport_share),
        }
        if self.action in required:
            name, val = required[self.action]
            if val is None:
                raise ValueError(f"{self.action.value} requires {name}")
        if self.action is ActionType.SCHEDULE and (self.amount is None or self.payee_id is None):
            raise ValueError("schedule requires an amount and a payee")
        if (
            self.action is ActionType.CONVERT
            and self.amount is not None
            and self.target_currency is self.amount.value.currency
        ):
            raise ValueError("convert needs a target currency different from the source")
        if self.route is not None and self.amount is not None:
            sent = sum(leg.send_amount.amount_minor for leg in self.route.legs)
            ccy = self.route.legs[0].send_amount.currency
            if sent != self.amount.value.amount_minor or ccy != self.amount.value.currency:
                raise ValueError("route legs must send exactly the proposal amount")
        return self

    def hashed_fields(self) -> dict[str, Any]:
        """Return exactly the fields the WYSIWYS hash covers (INV-5).

        Hashed (everything the user sees on the confirmation card, plus the replay guard):
        action, user_id, source account id, payee id, amount (minor units and currency),
        invoice_id, target currency, new payee terms, schedule terms, schedule id, budget,
        every route leg (provider, send, fee, rate, delivered, ETA), and the idempotency
        key. A schema version is included so a future change cannot collide.

        Deliberately NOT hashed:
        - provenance and source_ref of values: confirmation upgrades provenance after the
          user signs, and the hash must stay stable across that upgrade. Policy re-reads
          provenance at execution time (time-of-check equals time-of-use).
        - proposal_id: a bookkeeping label, not a financial term.
        - risk: advisory, recomputed at execution; users do not sign a risk score.
        - derived_from_docs: audit metadata, not a displayed field.
        - route aggregates (total cost, savings, explanation facts): derived from the legs,
          which are hashed, so they cannot differ without a leg differing.
        """
        extra: dict[str, Any] = {}
        if self.contribution is not None:  # only when present, so every other hash is unchanged
            extra["contribution"] = _values(self.contribution)
        if self.passport_share is not None:
            extra["passport_share"] = _values(self.passport_share)
        return {
            **extra,
            "v": HASH_SCHEMA_VERSION,
            "action": self.action,
            "user_id": self.user_id,
            "source_account_id": self.source_account_id.value,
            "payee_id": self.payee_id.value if self.payee_id is not None else None,
            "amount": self.amount.value if self.amount is not None else None,
            "invoice_id": self.invoice_id,
            "target_currency": self.target_currency,
            "new_payee": _values(self.new_payee),
            "schedule": _values(self.schedule),
            "schedule_id": self.schedule_id,
            "budget": self.budget,
            "route_legs": self.route.legs if self.route is not None else None,
            "idempotency_key": self.idempotency_key,
        }

    def canonical_hash(self) -> str:
        """SHA-256 over the canonical JSON of ``hashed_fields``."""
        return sha256_hex(self.hashed_fields())
