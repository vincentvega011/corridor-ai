"""Shared Pydantic contracts used across modules (docs/spec/contracts.md section 10).

Changed only deliberately (an ADR records any change): parallel sessions build against these.
"""

from corridor.contracts.action import (
    ActionProposal,
    ActionType,
    BudgetSpec,
    NewPayeeSpec,
    ScheduleSpec,
)
from corridor.contracts.canonical import canonical_bytes, canonical_json, sha256_hex
from corridor.contracts.confirm import (
    GRANT_TTL_SECONDS,
    ConfirmationGrant,
    ConfirmationLevel,
)
from corridor.contracts.documents import InvoiceDoc, MessageDoc, StatementDoc
from corridor.contracts.events import AgentEvent, AgentEventType
from corridor.contracts.intent import IntentFrame, IntentType, LanguageTag, SlotName
from corridor.contracts.ledger import (
    GENESIS_PREV_HASH,
    LedgerActor,
    LedgerEntry,
    LedgerEventType,
    compute_entry_hash,
    payload_hash_of,
)
from corridor.contracts.llm_outputs import (
    ComposerOutput,
    InvoiceFields,
    MessageFields,
    ParserOutput,
    PlannerOutput,
    PlannerStepDraft,
    ProposalDraft,
    ReaderOutput,
    StatementFields,
)
from corridor.contracts.money import (
    MINOR_UNIT_EXPONENT,
    CurrencyCode,
    Money,
    Rate,
    minor_unit_exponent,
)
from corridor.contracts.plan import MAX_PLAN_STEPS, Plan, PlanStep, StepKind
from corridor.contracts.policy import Decision, PolicyDecision, combine
from corridor.contracts.provenance import (
    TRUST_ORDER,
    Provenance,
    Tainted,
    derive,
    lowest_trust,
    trust_rank,
)
from corridor.contracts.risk import RiskAssessment, RiskLevel, RiskReason
from corridor.contracts.routing import RouteLeg, RouteRecommendation
from corridor.contracts.scalars import IdStr, Sha256Hex, ShortText
from corridor.contracts.tools import FactKind, NumericFact, ToolKind, ToolResult, ToolSpec

__all__ = [
    "GENESIS_PREV_HASH",
    "GRANT_TTL_SECONDS",
    "MAX_PLAN_STEPS",
    "MINOR_UNIT_EXPONENT",
    "TRUST_ORDER",
    "ActionProposal",
    "ActionType",
    "AgentEvent",
    "AgentEventType",
    "BudgetSpec",
    "ComposerOutput",
    "ConfirmationGrant",
    "ConfirmationLevel",
    "CurrencyCode",
    "Decision",
    "FactKind",
    "IdStr",
    "IntentFrame",
    "IntentType",
    "InvoiceDoc",
    "InvoiceFields",
    "LanguageTag",
    "LedgerActor",
    "LedgerEntry",
    "LedgerEventType",
    "MessageDoc",
    "MessageFields",
    "Money",
    "NewPayeeSpec",
    "NumericFact",
    "ParserOutput",
    "Plan",
    "PlanStep",
    "PlannerOutput",
    "PlannerStepDraft",
    "PolicyDecision",
    "ProposalDraft",
    "Provenance",
    "Rate",
    "ReaderOutput",
    "RiskAssessment",
    "RiskLevel",
    "RiskReason",
    "RouteLeg",
    "RouteRecommendation",
    "ScheduleSpec",
    "Sha256Hex",
    "ShortText",
    "SlotName",
    "StatementDoc",
    "StatementFields",
    "StepKind",
    "Tainted",
    "ToolKind",
    "ToolResult",
    "ToolSpec",
    "canonical_bytes",
    "canonical_json",
    "combine",
    "compute_entry_hash",
    "derive",
    "lowest_trust",
    "minor_unit_exponent",
    "payload_hash_of",
    "sha256_hex",
    "trust_rank",
]
