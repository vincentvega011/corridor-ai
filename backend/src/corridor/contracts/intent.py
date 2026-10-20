"""Intent frames produced by the NLU cascade (docs/spec/modules.md 5.3).

Security reason (INV-3, INV-4): an IntentFrame is built only from the user's own words.
Its slots are Tainted and the validator only permits USER_TYPED or MODEL_INFERRED, so a
value from a document can never arrive in the planner disguised as something the user said.
"""

from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from corridor.contracts.provenance import Provenance, Tainted
from corridor.contracts.scalars import Bps


class IntentType(StrEnum):
    """Closed set of intents. Anything else is ``other``."""

    SEND_MONEY = "send_money"
    SCHEDULE_TRANSFER = "schedule_transfer"
    CANCEL_SCHEDULE = "cancel_schedule"
    PAY_INVOICE = "pay_invoice"
    CHECK_BALANCE = "check_balance"
    CONVERT_CURRENCY = "convert_currency"
    COMPARE_ROUTES = "compare_routes"
    SET_BUDGET = "set_budget"
    SPENDING_SUMMARY = "spending_summary"
    ADD_PAYEE = "add_payee"
    CREDIT_PASSPORT_ISSUE = "credit_passport_issue"
    CREDIT_PASSPORT_SHARE = "credit_passport_share"
    EXPLAIN_DECISION = "explain_decision"
    HELP = "help"
    OTHER = "other"


class LanguageTag(StrEnum):
    """Detected language style of the user's message."""

    EN = "en"
    NE = "ne"  # Nepali, Devanagari
    NE_LATN = "ne_latn"  # romanised Nepali
    MIXED = "mixed"
    KO = "ko"  # Korean (reserved, language pack not built yet)
    ZH = "zh"  # Simplified Chinese (reserved, language pack not built yet)


class SlotName(StrEnum):
    """Closed set of slot names."""

    AMOUNT = "amount"
    CURRENCY = "currency"
    PAYEE_REF = "payee_ref"
    SOURCE_ACCOUNT = "source_account"
    DEADLINE = "deadline"
    RECURRENCE = "recurrence"
    CONDITION = "condition"
    PURPOSE = "purpose"


_ALLOWED_SLOT_PROVENANCE = frozenset({Provenance.USER_TYPED, Provenance.MODEL_INFERRED})


class IntentFrame(BaseModel):
    """Parsed user turn: language, intent, slots, missing slots, confidence (bps), parser used."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    language: LanguageTag
    intent: IntentType
    slots: dict[SlotName, Tainted[Any]] = Field(default_factory=dict)
    missing_slots: tuple[SlotName, ...] = ()
    confidence_bps: Bps
    parser: Literal["local", "cloud"]

    @model_validator(mode="after")
    def _slot_provenance(self) -> Self:
        for name, slot in self.slots.items():
            if slot.provenance not in _ALLOWED_SLOT_PROVENANCE:
                raise ValueError(
                    f"slot {name.value} has provenance {slot.provenance.value}; "
                    "intent slots may only be user_typed or model_inferred"
                )
        return self
