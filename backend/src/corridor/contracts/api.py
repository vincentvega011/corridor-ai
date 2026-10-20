"""The HTTP contract the customer app and the bank console build against (P3-1, ADR 0016).

Request and response bodies of every frozen endpoint (``docs/spec/api.md``), plus the typed ``data``
of the streamed events the UI renders. These models are the single source for the TypeScript types
in ``frontend/lib/contracts.ts`` (``make contracts``, never hand edited). Rules as everywhere in
this repo: integer minor units for money (strings such as ``"AUD 1,200.00"`` are display text
produced by ``corridor.formatting``, never parsed), basis points for scores, no floats, and no
field ever carries a secret, an MFA seed or a full account number (INV-12).

Event ``data`` models allow extra keys (``extra="allow"``): the server may add fields, a client
must ignore the ones it does not know.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from corridor.contracts.money import CurrencyCode
from corridor.contracts.provenance import Provenance
from corridor.contracts.risk import RiskLevel


class _Req(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Resp(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Data(BaseModel):
    """Typed ``data`` of a streamed event. Unknown extra keys are allowed (forward compatible)."""

    model_config = ConfigDict(extra="allow")


# --- requests
class Attachment(_Req):
    """A reference to a document the user uploaded through POST /attachments (their own only)."""

    doc_id: str = Field(pattern=r"^[0-9a-f]{64}$")


class StructuredAction(_Req):
    """What a screen button already knows (P4-2): no words to parse, no planner to ask.

    The server builds the same proposal the chat path would, from stored records; it goes through
    policy, grant, MFA and the executor as before. ``language`` is the language of the screen."""

    kind: Literal["pay_invoice", "send_money"]
    invoice_id: str | None = Field(default=None, max_length=64)
    payee_id: str | None = Field(default=None, max_length=64)
    amount_minor: int | None = Field(default=None, gt=0, le=10**12)
    currency: CurrencyCode | None = None
    language: Literal["en", "ne"] = "en"

    @model_validator(mode="after")
    def _complete(self) -> "StructuredAction":
        if self.kind == "pay_invoice" and not self.invoice_id:
            raise ValueError("pay_invoice needs invoice_id")
        if self.kind == "send_money" and not (
            self.payee_id and self.amount_minor and self.currency
        ):
            raise ValueError("send_money needs payee_id, amount_minor and currency")
        return self


class ChatRequest(_Req):
    """Body of POST /chat. There is deliberately no user id field: the user comes from the token."""

    message: str = Field(default="", max_length=2000)
    forwarded: str | None = Field(default=None, max_length=2000)
    """Pasted or forwarded third party text, marked explicitly by the user: reader only (INV-3)."""
    thread_id: str | None = Field(default=None, max_length=64, pattern=r"^[0-9a-f]{32}$")
    attachments: list[Attachment] = Field(default_factory=list, max_length=5)
    action: StructuredAction | None = None
    """A button flow: when set, ``message`` is ignored and no model is called to understand it."""

    @model_validator(mode="after")
    def _something_was_sent(self) -> "ChatRequest":
        if not (
            self.message.strip()
            or self.attachments
            or (self.forwarded or "").strip()
            or self.action
        ):
            raise ValueError("send a message, a document or forwarded text")
        return self


class ConfirmRequest(_Req):
    """Body of POST /chat/{thread}/confirm: the exact hash shown, plus a code when MFA is needed."""

    proposal_hash: str = Field(min_length=1, max_length=128)
    totp: str | None = Field(default=None, max_length=12)


class UnlockRequest(_Req):
    """Body of POST /unlock: the authenticator code that lifts the safety lock."""

    totp: str = Field(min_length=1, max_length=12)


class RouteCompareRequest(_Req):
    """Body of POST /routes/compare."""

    from_currency: CurrencyCode
    to_currency: CurrencyCode
    amount_minor: int = Field(gt=0, le=10**12)
    deadline: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    """Latest arrival day, ISO date. Counted conservatively in whole days from today."""


class PassportPresentRequest(_Req):
    """Body of POST /passport/present."""

    passport_id: str = Field(min_length=1, max_length=64)
    claims: list[str] = Field(min_length=1, max_length=8)
    audience: str = Field(default="the person who asked", min_length=1, max_length=60)
    challenge: str | None = Field(default=None, min_length=8, max_length=64)
    """The nonce the verifier issued for this audience (``POST /passport/challenge``). The
    presentation
    answers it. Without one the system issues a holder challenge (chat shares)."""


class PassportConfirmRequest(_Req):
    """Body of POST /passport/confirm."""

    proposal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    totp: str | None = Field(default=None, max_length=8)


class PassportVerifyRequest(_Req):
    """Body of POST /passport/verify (public). ``expected_audience`` is who the verifier is: a
    presentation
    made for someone else is refused."""

    presentation: dict[str, Any]
    expected_audience: str = Field(min_length=1, max_length=60)

    @model_validator(mode="after")
    def _bounded(self) -> "PassportVerifyRequest":
        import json as _json

        if len(_json.dumps(self.presentation)) > 20_000:
            raise ValueError("presentation too large")
        return self


class OfficerVerifyPayeeRequest(_Req):
    """Body of POST /officer/payees/{payee_id}/verify: an out-of-band method and a reference,
    both required.
    The Confirmation of Payee result is computed by the server and must be a match (ADR 0019)."""

    method: Literal[
        "callback_independent_number", "in_branch_check", "university_housing_office_list"
    ]
    reference: str = Field(min_length=3, max_length=200)


# --- shared views
class RiskReasonView(_Resp):
    code: str
    text: str
    contribution_bps: int


class RiskView(_Resp):
    """Deterministic risk score of a proposal (``corridor.risk``): never a model."""

    score_bps: int = Field(ge=0, le=10000)
    level: RiskLevel
    model_id: str
    reasons: list[RiskReasonView]


class ProposalCard(_Resp):
    """What the confirmation card shows: the exact hashed fields and the human readable display."""

    hash: str
    hashed_fields: dict[str, Any]
    display: dict[str, Any]
    route: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    amount_provenance: str | None = None


class AccountView(_Resp):
    account_id: str
    currency: CurrencyCode
    balance: str
    account_no: str
    status: str
    kind: Literal["spending", "savings"] = "spending"
    label: str | None = None
    """The account in words: currency, kind and masked number."""


class AccountsResponse(_Resp):
    accounts: list[AccountView]


class PayeeView(_Resp):
    """A saved payee. A payee made from a document or message shows the FIXED label
    ``Payee from document ****NNNN`` as ``display_name``; the sanitised name from the document is
    never put into a prompt and is shown only by deterministic rendering with the note "Name taken
    from document, not verified"."""

    payee_id: str
    display_name: str
    name_untrusted: bool
    region: str
    currency: CurrencyCode
    account_no: str
    verified: bool
    added_via: str
    provider_ref: str | None = None
    relation: str | None = None
    provenance: Provenance
    hold_until: str | None = None
    """ISO time (UTC) until which the cooling off cap applies to this account, or None."""
    verification: Literal["none", "pending", "verified", "refused"] = "none"
    """State of the customer's request that the bank verify this payee (ADR 0018)."""


class PayeesResponse(_Resp):
    payees: list[PayeeView]


class PendingProposal(_Resp):
    """A proposal waiting for the user's confirmation."""

    proposal_hash: str
    kind: Literal["chat", "passport"]
    thread_id: str | None = None
    required_level: Literal["confirm", "mfa"] | None = None
    card: ProposalCard | dict[str, Any]
    risk: RiskView | None = None


class PendingProposalsResponse(_Resp):
    proposals: list[PendingProposal]


# --- routes, tuition
class RouteOptionView(_Resp):
    route: str
    legs: list[dict[str, str]]
    total_cost: str
    received: str
    eta_hours: int
    arrives_by: str
    arrives_at: str | None = None
    """UTC instant of arrival: sandbox clock plus the route's speed (show it in the user's zone)."""
    meets_deadline: bool
    note: str | None = None
    extra_saving: str | None = None


class RouteComparisonResponse(_Resp):
    """Result of the bounded route comparison (ADR 0013). ``recommended`` is the cheapest single
    that meets the deadline; ``split_tip`` is a cheaper two transfer split, shown only as a tip."""

    found: bool
    pair: str | None = None
    status: Literal["ok", "no_route", "deadline_unmet"] | None = None
    amount: str | None = None
    mid_rate: str | None = None
    deadline: str | None = None
    recommended: RouteOptionView | None = None
    alternatives: list[RouteOptionView] = Field(default_factory=list)
    mid_rate_precise: str | None = None
    """The rate to 4 decimals without trailing zeros, for the Routes screen. ``mid_rate`` keeps the
    4 significant digit form the grounding verifier checks."""
    rate_date: str | None = None
    rate_source: str | None = None
    """Where ``mid_rate`` comes from (the seeded NRB reference table) and its date; illustrative."""
    saving_vs_most_expensive: str | None = None
    split_tip: RouteOptionView | None = None
    fastest_available: RouteOptionView | None = None
    note: str | None = None


class TuitionStepView(_Resp):
    step: int
    type: Literal["request_contribution", "pay_invoice"]
    from_user: str | None = None
    from_name: str | None = None
    from_relation: str | None = None
    from_region: str | None = None
    from_account: str | None = None
    from_account_label: str | None = None
    target_account: str | None = None
    target_account_label: str | None = None
    kind: str | None = None
    request: str | None = None
    request_minor: int | None = None
    request_currency: CurrencyCode | None = None
    arrives: str | None = None
    arrives_minor: int | None = None
    route: str | None = None
    fee: str | None = None
    eta_hours: int | None = None
    amount: str | None = None
    amount_minor: int | None = None


class TuitionPlanResponse(_Resp):
    """Result of the CP-SAT funding plan (ADR 0014). Each step is its own proposal and grant."""

    found: bool
    invoice_id: str | None = None
    invoice_label: str | None = None
    """The invoice in words (issuer and description); the id is not shown to customers."""
    status: Literal["ok", "infeasible", "no_plan"] | None = None
    currency: CurrencyCode | None = None
    amount: str | None = None
    still_owed: str | None = None
    due_date: str | None = None
    rate_date: str | None = None
    rate_source: str | None = None
    """The exchange rate behind the family steps (oldest date used); None when no step converts."""
    steps: list[TuitionStepView] = Field(default_factory=list)
    total_fees: str | None = None
    paid_now: str | None = None
    after_family_arrives: str | None = None


# --- passport
class PassportClaimView(_Resp):
    id: str
    shows: str
    facts: dict[str, int] = Field(default_factory=dict)
    """The holder's own counts for this claim; never sent to a model."""


class PassportView(_Resp):
    passport_id: str
    claims: list[PassportClaimView]
    issued_at: str
    expires_at: str


class PassportListResponse(_Resp):
    passports: list[PassportView]
    issuer_public_key: str


class PassportProposalResponse(_Resp):
    """Answer of POST /passport/issue and /passport/present: a proposal to confirm."""

    proposal_hash: str
    required_level: Literal["confirm", "mfa"]
    rule_ids: list[str]
    reasons: list[str]
    card: ProposalCard


class PassportConfirmResponse(_Resp):
    kind: Literal["issued", "shared"]
    passport_id: str
    claims: list[str]
    expires_at: str
    audience: str | None = None
    presentation: dict[str, Any] | None = None


class PassportVerifyResponse(_Resp):
    valid: bool
    reason: str | None = None
    audience: str | None = None
    holder: str | None = None
    expires_at: str | None = None
    claims: list[dict[str, Any]] = Field(default_factory=list)


# --- officer
class LedgerEntryView(_Resp):
    seq: int
    ts: str
    trace_id: str
    actor: str
    event_type: str
    payload: dict[str, Any]
    payload_hash: str
    policy_version_hash: str
    prev_hash: str
    entry_hash: str


class ChainStatus(_Resp):
    valid: bool
    entries_checked: int
    first_broken_seq: int | None = None
    reason: str | None = None


class LedgerViewResponse(_Resp):
    """The whole ledger (all users) with the result of recomputing the hash chain."""

    entries: list[LedgerEntryView]
    chain: ChainStatus


class DecisionView(_Resp):
    seq: int
    decision: str
    rule_ids: list[str]
    reasons: list[str]
    rule_results: list[list[str]] = Field(default_factory=list)
    """Each rule that spoke: [rule id, its own decision, its reason key]."""
    policy_version_hash: str
    risk: RiskView | None = None


class ProposalTraceResponse(_Resp):
    """Everything that happened to one proposal: the proposal, each rule decision with rule ids, the
    risk reasons, the confirmation grant and the execution (all from the hash chained ledger)."""

    found: bool
    proposal_hash: str
    user_id: str | None = None
    proposal: dict[str, Any] | None = None
    decisions: list[DecisionView] = Field(default_factory=list)
    grant: dict[str, Any] | None = None
    execution: dict[str, Any] | None = None
    rejections: list[dict[str, Any]] = Field(default_factory=list)
    entries: list[LedgerEntryView] = Field(default_factory=list)
    trace_entries: list[LedgerEntryView] = Field(default_factory=list)
    """Every ledger entry of the same turn (reader, parser cascade, proposal, decision, grant,
    MFA, execution)."""
    parser_outputs: dict[str, str | None] = Field(default_factory=dict)
    """Parser output reference hash (from the ledger) to its masked text; None once it has expired
    (retention in ``corridor.sandbox.parser_trace``)."""
    chain: ChainStatus | None = None


class RiskQueueItem(_Resp):
    seq: int
    ts: str
    user_id: str | None
    proposal_hash: str
    action: str | None = None
    decision: str
    rule_ids: list[str]
    risk: RiskView | None = None
    executed: bool = False


class RiskQueueResponse(_Resp):
    """Recent denied proposals and proposals scored high or critical, newest first."""

    items: list[RiskQueueItem]


class CaseSide(_Resp):
    mode: str
    user_model: str
    outcome: str | None = None
    attack_success: bool | None = None
    unauthorized: bool | None = None
    final_reply: str | None = None
    rule_ids: list[str] = Field(default_factory=list)
    new_transfers: int = 0
    unauthorized_npr: str | None = None
    """Unauthorized money moved, NPR with two decimals, from the stored result."""
    hits: list[str] = Field(default_factory=list)


class CaseComparisonResponse(_Resp):
    """Baseline against the full system for one case id, from the latest merged report."""

    found: bool
    case_id: str
    report: str | None = None
    report_stamp: str | None = None
    report_finished: str | None = None
    category: str | None = None
    baseline: list[CaseSide] = Field(default_factory=list)
    full: list[CaseSide] = Field(default_factory=list)


class CaseListItem(_Resp):
    id: str
    suite: str
    category: str | None = None
    control: bool = False


class CaseListResponse(_Resp):
    """The case ids of the latest report that have both a baseline and a full result."""

    found: bool
    report: str | None = None
    report_stamp: str | None = None
    report_finished: str | None = None
    cases: list[CaseListItem] = Field(default_factory=list)


class HeadlineRow(_Resp):
    user_model: str
    system: str
    blocked: int
    contained: int
    harmful: int
    npr_moved: str
    task_completion: str
    false_block: str
    attacks: int
    tasks: int


class EvalSummaryResponse(_Resp):
    """Headline numbers of the latest merged report (the same numbers as docs/evaluation.md)."""

    found: bool
    report: str | None = None
    stamp: str | None = None
    finished: str | None = None
    headline: list["HeadlineRow"] = Field(default_factory=list)
    """The headline table, one row per user model and system, every value read from the report."""
    summary: dict[str, Any] | None = None
    summary_by_user_model: dict[str, Any] | None = None


class VerificationRequestResponse(_Resp):
    """The customer asked the bank to verify a payee; only an officer can resolve it."""

    request_id: str
    payee_id: str
    status: Literal["pending", "verified", "refused"]


class OfficerVerificationItem(_Resp):
    """One open request in the officer queue. ``payee_name`` is plain text to be rendered as
    such."""

    request_id: str
    user_id: str
    payee_id: str
    payee_name: str
    account_masked: str
    currency: CurrencyCode
    provenance: Provenance
    cop: Literal["match", "mismatch", "no_record"]
    created_at: str
    status: Literal["pending", "verified", "refused"]


class OfficerVerificationQueueResponse(_Resp):
    items: list[OfficerVerificationItem]


class ContributionRespondRequest(_Req):
    """Body of POST /contributions/{id}/respond: the contributor accepts or declines."""

    accept: bool


class ContributionView(_Resp):
    """A request for family money. Accepting moves no money in the API: the contributor sends
    it from
    their own account in the normal way (INV-1); the status only tells the requester."""

    request_id: str
    requester: str
    contributor: str
    requester_region: str | None = None
    contributor_region: str | None = None
    contributor_relation: str | None = None
    amount: str
    currency: CurrencyCode
    invoice_id: str | None = None
    status: Literal["pending", "accepted", "declined"]
    created_at: str


class ContributionsResponse(_Resp):
    incoming: list[ContributionView]
    outgoing: list[ContributionView]


class ClockResponse(_Resp):
    """The sandbox clock. ``demo`` is true when it is fixed to a documented demo date (the UI
    says so)."""

    demo: bool
    now: str
    date: str
    label: str | None = None
    sandbox: bool = False
    """True only for APP_ENV dev or demo: the apps may then show the persona switcher and the
    sandbox authenticator. False in any other environment, which is the default."""


class PassportChallengeRequest(_Req):
    """Body of POST /passport/challenge (public): the verifier names itself and gets a nonce."""

    audience: str = Field(min_length=1, max_length=60)


class PassportChallengeResponse(_Resp):
    nonce: str
    audience: str
    expires_at: str


class OfficerVerifyPayeeResponse(_Resp):
    verified: bool
    payee_id: str
    officer_id: str


# --- event data
class ConfirmRequestData(_Data):
    """``confirm_request``: the card plus what is needed to confirm it."""

    hash: str
    proposal_hash: str
    hashed_fields: dict[str, Any]
    display: dict[str, Any]
    route: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    amount_provenance: str | None = None
    required_level: Literal["confirm", "mfa"] | None = None
    policy_reasons: list[str] = Field(default_factory=list)
    rule_ids: list[str] = Field(default_factory=list)
    prompt: str
    plan_step: dict[str, int] | None = None


class PolicyDecisionData(_Data):
    decision: Literal["require_confirm", "require_mfa", "deny"]
    rule_ids: list[str]
    reason_keys: list[str]
    reasons: list[str]
    policy_version_hash: str
    proposal_hash: str
    required_level: Literal["confirm", "mfa"] | None = None
    risk: RiskView | None = None


class ExecutedData(_Data):
    """``executed``: a payment, a saved payee, a request, or a passport issue or share."""

    proposal_hash: str | None = None
    transfer_id: str | None = None
    amount: str | None = None
    payee: str | None = None
    balance_after: str | None = None
    action: str | None = None
    passport_id: str | None = None
    claims: list[str] | None = None
    presentation: dict[str, Any] | None = None
    audience: str | None = None


class RejectedData(_Data):
    reason: str
    rule_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    actions: list[dict[str, str]] = Field(default_factory=list)
    """Things the user can do next, e.g. ``{"kind": "request_verification", "payee_id":
    "..."}`` (ADR 0018)."""
    hold: dict[str, Any] | None = None
    """For the cooling off cap: ``until`` (ISO time), ``cap`` (display text), ``untrusted``
    (full hold)."""


class ClarifyData(_Data):
    slot: str
    question: str
    options: list[str] = Field(default_factory=list)


class MessageData(_Data):
    text: str
    source: str
    status: str | None = None
    hold: dict[str, Any] | None = None
    """After a payee was saved: ``until`` (ISO), ``cap``, ``untrusted`` (hold card, ADR 0018)."""
    actions: list[dict[str, str]] = Field(default_factory=list)


class RouteEventData(_Data):
    """``route``: the optimizer's choice for a transfer, or a funding plan."""

    label: str
    provider: str | None = None
    send: str | None = None
    cost: str | None = None
    delivered: str | None = None
    saving_vs_default: str | None = None
    eta_hours: int | None = None
    steps: list[dict[str, Any]] | None = None
    status: str | None = None


EVENT_DATA_MODELS: dict[str, type[_Data]] = {
    "confirm_request": ConfirmRequestData,
    "policy_decision": PolicyDecisionData,
    "executed": ExecutedData,
    "rejected": RejectedData,
    "clarify": ClarifyData,
    "message": MessageData,
    "route": RouteEventData,
}

API_MODELS: tuple[type[BaseModel], ...] = (
    ChatRequest,
    StructuredAction,
    Attachment,
    ConfirmRequest,
    UnlockRequest,
    RouteCompareRequest,
    PassportPresentRequest,
    PassportConfirmRequest,
    PassportVerifyRequest,
    OfficerVerifyPayeeRequest,
    VerificationRequestResponse,
    OfficerVerificationItem,
    OfficerVerificationQueueResponse,
    ClockResponse,
    ContributionRespondRequest,
    ContributionView,
    ContributionsResponse,
    PassportChallengeRequest,
    PassportChallengeResponse,
    RiskView,
    RiskReasonView,
    ProposalCard,
    AccountView,
    AccountsResponse,
    PayeeView,
    PayeesResponse,
    PendingProposal,
    PendingProposalsResponse,
    RouteOptionView,
    RouteComparisonResponse,
    TuitionStepView,
    TuitionPlanResponse,
    PassportClaimView,
    PassportView,
    PassportListResponse,
    PassportProposalResponse,
    PassportConfirmResponse,
    PassportVerifyResponse,
    LedgerEntryView,
    ChainStatus,
    LedgerViewResponse,
    DecisionView,
    ProposalTraceResponse,
    RiskQueueItem,
    RiskQueueResponse,
    CaseSide,
    CaseComparisonResponse,
    CaseListItem,
    CaseListResponse,
    HeadlineRow,
    EvalSummaryResponse,
    OfficerVerifyPayeeResponse,
    *EVENT_DATA_MODELS.values(),
)
