<!-- Core data contracts (section 10). Section numbers kept so old references still resolve. -->

## 10. Core data contracts (`corridor/contracts/`)

These are the real, implemented shapes (see ADR 0002 and 0003). Every model is Pydantic v2 with
`frozen=True, extra="forbid"`. Sequences are tuples. **No floats anywhere**: money is integer minor units,
rates are `Rate` (Decimal, 8 places, ROUND_HALF_EVEN), ratios and scores are basis points
(`Bps`, 0 to 10000). Shared scalars (`ShortText` max 120 chars, `IdStr`, `Sha256Hex`, `Bps`, `SignedBps`) live in
`scalars.py`. Files: `money, provenance, canonical, scalars, intent, plan, action, routing, risk, documents,
policy, confirm, ledger, events, tools, llm_outputs`.

```python
# money.py
class Money(BaseModel):
    amount_minor: int            # strict int: bool, float, numeric strings are rejected
    currency: CurrencyCode       # ISO 4217: NPR, AUD, QAR, MYR, CNY, USD

# provenance.py (no upgrade method exists; only corridor.confirm may create USER_CONFIRMED)
class Tainted[T](BaseModel):
    value: T; provenance: Provenance; source_ref: str | None = None
def lowest_trust(*values) -> Provenance   # UNTRUSTED < MODEL_INFERRED < USER_TYPED < SYSTEM

# intent.py
class IntentFrame(BaseModel):
    language: LanguageTag            # StrEnum: en, ne, ne_latn, mixed, ko, zh (ko, zh reserved for later)
    intent: IntentType
    slots: dict[SlotName, Tainted[Any]]   # provenance must be USER_TYPED or MODEL_INFERRED
    missing_slots: tuple[SlotName, ...]
    confidence_bps: Bps
    parser: Literal["local", "cloud"]

# plan.py
class PlanStep(BaseModel):
    id: str; kind: StepKind; tool: str
    args: dict[str, Tainted[Any]]; depends_on: tuple[str, ...] = ()
class Plan(BaseModel):
    steps: tuple[PlanStep, ...]      # validated: unique ids, no dangling deps, acyclic, <= 12

# routing.py
class RouteLeg(BaseModel):
    provider_id; send_amount: Money; fee: Money; effective_rate: Rate; delivered: Money; eta_hours: int
class RouteRecommendation(BaseModel):   # 1 or 2 legs; aggregates are validated against the legs
    legs; total_cost: Money; effective_rate: Rate; delivered: Money; eta_hours: int
    savings_vs_default: Money; explanation_facts: tuple[NumericFact, ...]

# risk.py
class RiskAssessment(BaseModel):
    score_bps: Bps; level: RiskLevel   # LOW | HIGH | CRITICAL
    top_reasons: tuple[RiskReason, ...]  # max 3; RiskReason(feature, text, contribution_bps)
    model_id: str; fallback_used: bool

# action.py
class ActionProposal(BaseModel):
    proposal_id: str; action: ActionType; user_id: str
    source_account_id: Tainted[str]
    payee_id: Tainted[str] | None
    amount: Tainted[Money] | None    # must equal the sum of route legs when a route is present
    invoice_id: str | None           # PAY_INVOICE (hashed)
    target_currency: CurrencyCode | None   # CONVERT
    new_payee: NewPayeeSpec | None   # ADD_PAYEE: Tainted name, region, account_no + currency
    schedule: ScheduleSpec | None    # SCHEDULE: Tainted rrule, first_run
    schedule_id: str | None          # CANCEL_SCHEDULE
    budget: BudgetSpec | None        # SET_BUDGET
    route: RouteRecommendation | None
    risk: RiskAssessment | None
    derived_from_docs: tuple[Sha256Hex, ...] = ()
    idempotency_key: str
    def canonical_hash(self) -> str: ...   # HASH_SCHEMA_VERSION 2; see hashed_fields() docstring

# documents.py (reader output; every Tainted field is forced to UNTRUSTED by a validator)
class InvoiceDoc / MessageDoc / StatementDoc  # + source_doc_hash, injection_suspected,
                                              #   contains_payment_instructions

# policy.py
class Decision(StrEnum): ALLOW; REQUIRE_CONFIRM; REQUIRE_MFA; DENY   # Decision.rank, combine()
class PolicyDecision(BaseModel):
    decision: Decision; rule_ids: tuple[str, ...]   # at least one
    reasons: tuple[str, ...]; policy_version_hash: Sha256Hex

# confirm.py
class ConfirmationGrant(BaseModel):   # expires_at must equal issued_at + 120 s; aware datetimes
    grant_id; proposal_hash; user_id; level: ConfirmationLevel; issued_at; expires_at; used: bool

# ledger.py
class LedgerEntry(BaseModel):         # ts must be timezone aware
    seq (>= 1); ts; trace_id; actor; event_type; payload_canonical_json
    payload_hash; policy_version_hash; prev_hash; entry_hash
# entry_hash = sha256_hex([prev_hash, seq, ts, trace_id, actor, event_type, payload_hash,
#                          policy_version_hash])   (deviation from 5.10 text: also covers
#                          trace_id, actor, event_type; see ADR 0002). payload_hash_of() hashes the text.

# events.py
class AgentEvent(BaseModel):
    type: AgentEventType; trace_id: str; seq: int; ts: datetime; data: dict[str, Any]

# tools.py
class ToolSpec(BaseModel): name; description; kind: ToolKind = READ (the only kind); args_schema
class ToolResult(BaseModel): tool_call_id; tool; data; facts: tuple[NumericFact, ...]

# llm_outputs.py: THE LLM OUTPUT BOUNDARY
# ParserOutput, PlannerOutput (PlannerStepDraft), ProposalDraft, ReaderOutput (Invoice/Message/StatementFields)
# hold PLAIN values only, with no provenance field and extra="forbid". Our code attaches provenance when it
# converts drafts into the contracts above. ProposalDraft has no user_id, idempotency_key, risk or route, and
# cannot restate an amount or payee when invoice_ref is set (the invoice supplies them, with UNTRUSTED taint).
```

TypeScript mirrors live in `frontend/lib/contracts.ts`, generated from the FastAPI OpenAPI schema (`make contracts`), never hand-edited.
