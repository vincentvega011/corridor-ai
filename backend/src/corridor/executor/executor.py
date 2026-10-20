"""The only writer of sandbox state (docs/spec/modules.md 5.9, INV-1, INV-5, INV-6, INV-8, INV-10).

One database transaction does all of this, and commits it together or not at all:

1. serialise on the idempotency key; a replay with identical terms returns the earlier result and a
   reuse with different terms is rejected (INV-6);
2. lock the source account row;
3. build a fresh ``AccountState`` from stored rows and run the policy again, so the decision is the
   one in force at execution time (time of check equals time of use). Provenance is read from the
   database columns and can only lower what the proposal carries (INV-4);
4. consume the confirmation grant: right hash, user and level, unused, unexpired (INV-5);
5. debit atomically (never below zero), write the transfer, mark an invoice paid;
6. append the execution entry to the ledger (INV-8).

On any failure the transaction rolls back and a failure entry is appended in a separate transaction;
the caller gets a short reason code and no partial effect (INV-10).
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from corridor.ablation import NONE, Ablation, Ablations
from corridor.ablation_hooks import evaluate_ablated, unbound_grant
from corridor.confirm.grants import GrantError, consume_grant
from corridor.confirm.lock import record_event, record_policy_denial_docs
from corridor.confirm.upgrade import confirm_values
from corridor.contracts.action import ActionProposal, ActionType
from corridor.contracts.confirm import ConfirmationLevel
from corridor.contracts.ledger import LedgerActor, LedgerEventType
from corridor.contracts.policy import Decision, PolicyDecision
from corridor.contracts.provenance import Provenance, lowest_trust
from corridor.ledger import Ledger, compute_roots
from corridor.policy.config import PolicyConfig
from corridor.policy.rules import HIGH_RISK_RULES
from corridor.sandbox.account_state import build_account_state
from corridor.sandbox.doc_payee import payee_terms
from corridor.sandbox.models import (
    PASSPORT_CHALLENGE_MINUTES,
    Account,
    ConfirmationGrantRow,
    ContributionRequest,
    DocumentRow,
    Invoice,
    Passport,
    PassportChallenge,
    PassportPresentation,
    Payee,
    Transfer,
)

log = structlog.get_logger("corridor.executor")

SUPPORTED = (
    ActionType.TRANSFER,
    ActionType.PAY_INVOICE,
    ActionType.ADD_PAYEE,
    ActionType.REQUEST_CONTRIBUTION,
    ActionType.ISSUE_PASSPORT,
    ActionType.SHARE_PASSPORT,
)
_GRANT_FAILURES = {"hash_mismatch", "wrong_user", "already_used", "expired", "level_too_low"}


class ExecutionRejected(Exception):
    """Execution was refused or failed safely. ``reason`` is a short machine readable code."""

    def __init__(self, reason: str, rule_ids: tuple[str, ...] = ()) -> None:
        super().__init__(reason)
        self.reason = reason
        self.rule_ids = rule_ids


@dataclass(frozen=True)
class ExecutionResult:
    """What the executor did (or, for a replay, what it did earlier)."""

    transfer_id: str
    amount_minor: int
    currency: str
    payee_id: str
    from_account: str
    balance_after_minor: int
    replayed: bool = False
    rule_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PayeeAdded:
    """A payee saved from a document: untrusted, on hold until ``hold_until`` (R-PAYEE-COOLOFF)."""

    payee_id: str
    display_name: str
    account_masked: str
    currency: str
    hold_until: datetime
    rule_ids: tuple[str, ...] = ()
    provenance: str = "untrusted"


@dataclass(frozen=True)
class ContributionRequested:
    """A request for money was stored for a family member. No money moved (INV-1)."""

    request_id: str
    contributor_user_id: str
    amount_minor: int
    currency: str
    rule_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PassportDone:
    """A passport was issued or shared. No money moved. ``presentation`` is set when shared."""

    kind: str  # issued | shared
    passport_id: str
    claim_ids: tuple[str, ...]
    expires_at: str
    audience: str | None = None
    presentation: dict[str, Any] | None = None
    rule_ids: tuple[str, ...] = ()


def required_level(decision: PolicyDecision) -> ConfirmationLevel:
    """The grant level a decision needs. Anything unexpected needs the strongest level."""
    if decision.decision is Decision.REQUIRE_CONFIRM:
        return ConfirmationLevel.CONFIRM
    return ConfirmationLevel.MFA


class Executor:
    """Executes confirmed proposals. Holds no mutable state."""

    def __init__(
        self,
        engine: Engine,
        config: PolicyConfig,
        ledger: Ledger | None = None,
        ablations: Ablations = NONE,
        secrets_dir: Path | None = None,
    ) -> None:
        self.engine, self.config, self.ledger = engine, config, ledger or Ledger()
        self.secrets_dir = secrets_dir  # where the passport signing key lives (None: the default)
        self.ablations = ablations  # evaluation only (ADR 0010); NONE in every real run

    def execute(
        self,
        proposal: ActionProposal,
        grant_id: str,
        *,
        user_id: str,
        now: datetime,
        trace_id: str,
        session_id: str | None = None,
    ) -> ExecutionResult | PayeeAdded | ContributionRequested | PassportDone:
        """Run ``proposal`` for ``user_id`` with the grant the user was issued, or raise
        ``ExecutionRejected``. ``user_id`` is the authenticated user, never from the proposal."""
        try:
            result = self._run(proposal, grant_id, user_id, now, trace_id, session_id)
            self._anchor(now)
            return result
        except ExecutionRejected as exc:
            self._record_failure(proposal, user_id, exc, now, trace_id, session_id)
            raise
        except Exception as exc:  # fail closed on anything unexpected (INV-10)
            log.error("execution_error", error=type(exc).__name__)
            rejected = ExecutionRejected("execution_error")
            self._record_failure(proposal, user_id, rejected, now, trace_id, session_id)
            raise rejected from exc

    def _anchor(self, now: datetime) -> None:
        """Store fresh Merkle roots after a commit, so deleting the newest entries is detectable.
        Best effort: the payment already committed and a failure here is only logged."""
        try:
            with Session(self.engine) as s, s.begin():
                compute_roots(s, now)
        except Exception as exc:
            log.error("anchor_failed", error=type(exc).__name__)

    # ------------------------------------------------------------------------------------------
    def _run(
        self,
        proposal: ActionProposal,
        grant_id: str,
        user_id: str,
        now: datetime,
        trace_id: str,
        session_id: str | None,
    ) -> ExecutionResult | PayeeAdded | ContributionRequested | PassportDone:
        proposal_hash = proposal.canonical_hash()
        if proposal.user_id != user_id:
            raise ExecutionRejected("wrong_user")
        if proposal.action not in SUPPORTED:
            raise ExecutionRejected("unsupported_action")
        if proposal.action is ActionType.ADD_PAYEE:
            return self._run_add_payee(
                proposal, proposal_hash, grant_id, user_id, now, trace_id, session_id
            )
        if proposal.action in (ActionType.ISSUE_PASSPORT, ActionType.SHARE_PASSPORT):
            return self._run_passport(
                proposal, proposal_hash, grant_id, user_id, now, trace_id, session_id
            )
        if proposal.action is ActionType.REQUEST_CONTRIBUTION:
            return self._run_request_contribution(
                proposal, proposal_hash, grant_id, user_id, now, trace_id, session_id
            )
        if proposal.amount is None or proposal.payee_id is None:
            raise ExecutionRejected("incomplete_proposal")
        amount, account_id, payee_id = (
            proposal.amount.value,
            proposal.source_account_id.value,
            proposal.payee_id.value,
        )
        transfer_id = "tr_" + hashlib.sha256(proposal.idempotency_key.encode()).hexdigest()[:20]
        try:
            with Session(self.engine) as s, s.begin():
                s.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                    {"k": "idem:" + proposal.idempotency_key},
                )
                s.execute(  # one user's payments are decided one at a time (daily limit, velocity)
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                    {"k": "user:" + user_id},
                )
                earlier = s.scalars(
                    select(Transfer).where(Transfer.idempotency_key == proposal.idempotency_key)
                ).first()
                if earlier is not None:
                    return self._replay(
                        s, earlier, proposal, proposal_hash, user_id, grant_id, now, trace_id,
                        session_id,
                    )  # fmt: skip
                acct = s.scalars(
                    select(Account)
                    .where(Account.id == account_id, Account.user_id == user_id)
                    .with_for_update()
                ).first()
                if (
                    acct is None
                    or acct.status != "active"
                    or acct.currency != amount.currency.value
                ):
                    raise ExecutionRejected("account_not_allowed")
                state = build_account_state(s, user_id, now, self.config)
                decision = evaluate_ablated(self.ablations, proposal, state, self.config, now)
                if decision.decision is Decision.DENY:
                    raise ExecutionRejected("policy_denied", decision.rule_ids)
                if self.ablations.has(Ablation.NO_GRANTS):  # evaluation only: no grant is needed
                    grant = unbound_grant(user_id, proposal_hash, now)
                else:
                    try:
                        grant = consume_grant(
                            s, grant_id=grant_id, proposal_hash=proposal_hash, user_id=user_id,
                            minimum=required_level(decision), now=now,
                        )  # fmt: skip
                    except GrantError as exc:
                        raise ExecutionRejected(f"grant_{exc.code}", decision.rule_ids) from exc
                confirmed = confirm_values(proposal, grant, now)
                invoice = None
                if proposal.action is ActionType.PAY_INVOICE:
                    invoice = s.scalars(
                        select(Invoice)
                        .where(
                            Invoice.id == proposal.invoice_id,
                            Invoice.user_id == user_id,
                            Invoice.status.in_(("open", "part_paid")),
                        )
                        .with_for_update()
                    ).first()
                    if invoice is None or invoice.beneficiary_payee_id != payee_id:
                        raise ExecutionRejected("invoice_mismatch")
                    if (
                        amount.currency.value != invoice.currency
                        or amount.amount_minor > invoice.amount_minor - invoice.paid_minor
                    ):  # an instalment can never overpay the invoice (policy checks this too)
                        raise ExecutionRejected("invoice_mismatch")
                debited = s.execute(
                    text(
                        "UPDATE accounts SET balance_minor = balance_minor - :amt "
                        "WHERE id = :a AND user_id = :u AND balance_minor >= :amt "
                        "RETURNING balance_minor"
                    ),
                    {"amt": amount.amount_minor, "a": account_id, "u": user_id},
                ).first()
                if debited is None:
                    raise ExecutionRejected("insufficient_funds")
                route = (
                    proposal.route.model_dump(mode="json")
                    if proposal.route is not None
                    else {"type": "domestic"}
                )
                s.add(
                    Transfer(
                        id=transfer_id, user_id=user_id, from_account=account_id, payee_id=payee_id,
                        amount_minor=amount.amount_minor, currency=amount.currency.value,
                        route_json=route, status="executed",
                        idempotency_key=proposal.idempotency_key, proposal_hash=proposal_hash,
                        created_at=now, executed_at=now,
                    )
                )  # fmt: skip
                if invoice is not None:
                    invoice.paid_minor += amount.amount_minor
                    invoice.status = (
                        "paid" if invoice.paid_minor == invoice.amount_minor else "part_paid"
                    )
                s.flush()
                balance_after = int(debited[0])
                self._append(
                    s, LedgerEventType.EXECUTION, user_id, trace_id, session_id, now,
                    {
                        "proposal_hash": proposal_hash, "transfer_id": transfer_id,
                        "grant_id": grant.grant_id, "grant_level": grant.level.value,
                        "decision": decision.decision.value, "rule_ids": list(decision.rule_ids),
                        "amount_minor": amount.amount_minor, "currency": amount.currency.value,
                        "payee_id": payee_id, "from_account": account_id,
                        "invoice_id": proposal.invoice_id,
                        "payee_provenance_after_confirm": confirmed.payee_id.provenance.value
                        if confirmed.payee_id
                        else None,
                    },
                    decision.policy_version_hash,
                )  # fmt: skip
        except ExecutionRejected:
            raise
        except IntegrityError as exc:  # a concurrent duplicate hits the UNIQUE constraint
            raise ExecutionRejected("duplicate_idempotency_key") from exc
        return ExecutionResult(
            transfer_id=transfer_id, amount_minor=amount.amount_minor,
            currency=amount.currency.value, payee_id=payee_id, from_account=account_id,
            balance_after_minor=balance_after, rule_ids=decision.rule_ids,
        )  # fmt: skip

    def _run_add_payee(
        self,
        proposal: ActionProposal,
        proposal_hash: str,
        grant_id: str,
        user_id: str,
        now: datetime,
        trace_id: str,
        session_id: str | None,
    ) -> PayeeAdded:
        """Save a payee proposed from a stored document: always ``untrusted``, never verified.

        Everything that matters is re-read from the stored document, not from the proposal: the
        account hash and last four digits, the name and the currency. The proposal must agree
        with the stored document, or it is refused. A confirmation (even with MFA) cannot change
        the payee's origin: the row is written ``untrusted`` here and only time ends the cooling
        off (R-PAYEE-COOLOFF). INV-1, INV-4, INV-5, INV-8."""
        spec = proposal.new_payee
        docs = tuple(proposal.derived_from_docs)
        if spec is not None and not docs and spec.account_no.value.startswith("typed:"):
            return self._run_add_typed_payee(
                proposal, proposal_hash, grant_id, user_id, now, trace_id, session_id
            )
        if spec is None or len(docs) != 1:
            raise ExecutionRejected("incomplete_proposal")
        payee_id = "pay_doc_" + hashlib.sha256(proposal.idempotency_key.encode()).hexdigest()[:16]
        try:
            with Session(self.engine) as s, s.begin():
                s.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                    {"k": "user:" + user_id},
                )
                if s.get(Payee, payee_id) is not None:
                    raise ExecutionRejected("idempotency_key_reuse")
                row = s.get(DocumentRow, (docs[0], user_id))
                terms = payee_terms(dict(row.fields)) if row is not None else None
                if (
                    terms is None
                    or spec.account_no.value != terms.account_ref
                    or spec.display_name.value != terms.name
                    or spec.currency.value != terms.currency
                ):
                    raise ExecutionRejected("document_mismatch")
                if s.scalars(
                    select(Payee).where(
                        Payee.user_id == user_id, Payee.account_no_hash == terms.account_hash
                    )
                ).first():
                    raise ExecutionRejected("payee_exists")
                state = build_account_state(s, user_id, now, self.config)
                decision = evaluate_ablated(self.ablations, proposal, state, self.config, now)
                if decision.decision is Decision.DENY:
                    raise ExecutionRejected("policy_denied", decision.rule_ids)
                if self.ablations.has(Ablation.NO_GRANTS):  # evaluation only
                    grant = unbound_grant(user_id, proposal_hash, now)
                else:
                    try:
                        grant = consume_grant(
                            s, grant_id=grant_id, proposal_hash=proposal_hash, user_id=user_id,
                            minimum=required_level(decision), now=now,
                        )  # fmt: skip
                    except GrantError as exc:
                        raise ExecutionRejected(f"grant_{exc.code}", decision.rule_ids) from exc
                s.add(
                    Payee(
                        id=payee_id, user_id=user_id, display_name=terms.name, region=terms.region,
                        currency=terms.currency, relation=None,
                        account_no_masked="****" + terms.last4, account_no_hash=terms.account_hash,
                        verified_at=None, added_via="document", created_at=now,
                        provenance="untrusted",
                    )
                )  # fmt: skip
                s.flush()
                hold_until = now + timedelta(hours=self.config.payee_cooloff.hours)
                self._append(
                    s, LedgerEventType.EXECUTION, user_id, trace_id, session_id, now,
                    {
                        "proposal_hash": proposal_hash, "action": "add_payee", "payee_id": payee_id,
                        "grant_id": grant.grant_id, "grant_level": grant.level.value,
                        "decision": decision.decision.value, "rule_ids": list(decision.rule_ids),
                        "payee_provenance": "untrusted", "doc": docs[0][:16],
                        "name_sanitised": terms.name_sanitised,
                        "hold_until": hold_until.isoformat(timespec="minutes"),
                    },
                    decision.policy_version_hash,
                )  # fmt: skip
        except ExecutionRejected:
            raise
        except IntegrityError as exc:
            raise ExecutionRejected("duplicate_idempotency_key") from exc
        return PayeeAdded(
            payee_id=payee_id, display_name=terms.name, account_masked="****" + terms.last4,
            currency=terms.currency, hold_until=hold_until, rule_ids=decision.rule_ids,
        )  # fmt: skip

    def _run_add_typed_payee(
        self,
        proposal: ActionProposal,
        proposal_hash: str,
        grant_id: str,
        user_id: str,
        now: datetime,
        trace_id: str,
        session_id: str | None,
    ) -> PayeeAdded:
        """Save a payee the user typed in chat (ADR 0018). MFA is required by policy (R-PAYEE-ADD).

        The row records the least trusted provenance of the name and the account:
        ``user_typed`` only if
        the digits were in the user's own words, otherwise ``untrusted`` or ``model_inferred``
        (held in
        full by R-PAYEE-COOLOFF). The account arrives as a keyed hash and last four digits: the
        executor never sees the number. Never verified: only a bank officer verifies (ADR 0012)."""
        spec = proposal.new_payee
        assert spec is not None
        parts = spec.account_no.value.split(":")
        if len(parts) != 3 or len(parts[1]) != 64 or not parts[2].isdigit() or len(parts[2]) != 4:
            raise ExecutionRejected("incomplete_proposal")
        _, acct_hash, last4 = parts
        prov = lowest_trust(spec.display_name.provenance, spec.account_no.provenance)
        if prov not in (Provenance.USER_TYPED, Provenance.UNTRUSTED, Provenance.MODEL_INFERRED):
            raise ExecutionRejected("incomplete_proposal")
        payee_id = "pay_typed_" + hashlib.sha256(proposal.idempotency_key.encode()).hexdigest()[:16]
        try:
            with Session(self.engine) as s, s.begin():
                s.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                    {"k": "user:" + user_id},
                )
                if s.get(Payee, payee_id) is not None:
                    raise ExecutionRejected("idempotency_key_reuse")
                if s.scalars(
                    select(Payee).where(
                        Payee.user_id == user_id, Payee.account_no_hash == acct_hash
                    )
                ).first():
                    raise ExecutionRejected("payee_exists")
                state = build_account_state(s, user_id, now, self.config)
                decision = evaluate_ablated(self.ablations, proposal, state, self.config, now)
                if decision.decision is Decision.DENY:
                    raise ExecutionRejected("policy_denied", decision.rule_ids)
                if self.ablations.has(Ablation.NO_GRANTS):  # evaluation only
                    grant = unbound_grant(user_id, proposal_hash, now)
                else:
                    try:
                        grant = consume_grant(
                            s, grant_id=grant_id, proposal_hash=proposal_hash, user_id=user_id,
                            minimum=required_level(decision), now=now,
                        )  # fmt: skip
                    except GrantError as exc:
                        raise ExecutionRejected(f"grant_{exc.code}", decision.rule_ids) from exc
                name = spec.display_name.value
                s.add(
                    Payee(
                        id=payee_id, user_id=user_id, display_name=name, region=spec.region.value,
                        currency=spec.currency.value, relation=None, account_no_masked="****" +
                        last4,
                        account_no_hash=acct_hash, verified_at=None, added_via="agent_mfa",
                        created_at=now, provenance=prov.value,
                    )
                )  # fmt: skip
                s.flush()
                hold_until = now + timedelta(hours=self.config.payee_cooloff.hours)
                self._append(
                    s, LedgerEventType.EXECUTION, user_id, trace_id, session_id, now,
                    {
                        "proposal_hash": proposal_hash, "action": "add_payee", "payee_id": payee_id,
                        "grant_id": grant.grant_id, "grant_level": grant.level.value,
                        "decision": decision.decision.value, "rule_ids": list(decision.rule_ids),
                        "payee_provenance": prov.value, "origin": "chat",
                        "hold_until": hold_until.isoformat(timespec="minutes"),
                    },
                    decision.policy_version_hash,
                )  # fmt: skip
        except ExecutionRejected:
            raise
        except IntegrityError as exc:
            raise ExecutionRejected("duplicate_idempotency_key") from exc
        return PayeeAdded(
            payee_id=payee_id, display_name=name, account_masked="****" + last4,
            currency=spec.currency.value, hold_until=hold_until, rule_ids=decision.rule_ids,
            provenance=prov.value,
        )  # fmt: skip

    def _run_passport(
        self,
        proposal: ActionProposal,
        proposal_hash: str,
        grant_id: str,
        user_id: str,
        now: datetime,
        trace_id: str,
        session_id: str | None,
    ) -> PassportDone:
        """Issue a Credit Passport from the user's history, or share chosen claims (ADR 0015).

        The claims are computed here from the database, never taken from the proposal or a model.
        A share builds a presentation with only the confirmed claims, read from the stored passport.
        Nothing here moves money. INV-1, INV-5, INV-8, INV-12."""
        from corridor.passport import credential
        from corridor.passport.claims import build_claims
        from corridor.sandbox import vault

        issuing = proposal.action is ActionType.ISSUE_PASSPORT
        stem = "pp_" if issuing else "ppr_"
        row_id = stem + hashlib.sha256(proposal.idempotency_key.encode()).hexdigest()[:20]
        try:
            with Session(self.engine) as s, s.begin():
                s.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                    {"k": "user:" + user_id},
                )
                if s.get(Passport if issuing else PassportPresentation, row_id) is not None:
                    raise ExecutionRejected("idempotency_key_reuse")
                state = build_account_state(s, user_id, now, self.config)
                decision = evaluate_ablated(self.ablations, proposal, state, self.config, now)
                if decision.decision is Decision.DENY:
                    raise ExecutionRejected("policy_denied", decision.rule_ids)
                if self.ablations.has(Ablation.NO_GRANTS):  # evaluation only
                    grant = unbound_grant(user_id, proposal_hash, now)
                else:
                    try:
                        grant = consume_grant(
                            s, grant_id=grant_id, proposal_hash=proposal_hash, user_id=user_id,
                            minimum=required_level(decision), now=now,
                        )  # fmt: skip
                    except GrantError as exc:
                        raise ExecutionRejected(f"grant_{exc.code}", decision.rule_ids) from exc
                common = {
                    "proposal_hash": proposal_hash, "grant_id": grant.grant_id,
                    "grant_level": grant.level.value, "decision": decision.decision.value,
                    "rule_ids": list(decision.rule_ids),
                }  # fmt: skip
                if issuing:
                    claims = build_claims(s, user_id, now.date())
                    if not claims:
                        raise ExecutionRejected("no_payment_history")
                    issued = credential.issue(
                        claims, holder_seed=f"{user_id}:{row_id}",
                        key_hex=vault.passport_signing_key_hex(self.secrets_dir),
                        holder_key_hex=vault.passport_holder_key_hex(user_id, self.secrets_dir),
                        now=now,
                    )  # fmt: skip
                    s.add(
                        Passport(
                            id=row_id, user_id=user_id, token=issued.token,
                            disclosures=list(issued.disclosures), claim_ids=list(issued.claim_ids),
                            holder=issued.holder, issued_at=now, expires_at=issued.expires_at,
                            idempotency_key=proposal.idempotency_key,
                        )
                    )  # fmt: skip
                    s.flush()
                    self._append(
                        s, LedgerEventType.PASSPORT_ISSUED, user_id, trace_id, session_id, now,
                        {**common, "passport_id": row_id, "claim_ids": list(issued.claim_ids)},
                        decision.policy_version_hash,
                    )  # fmt: skip
                    return PassportDone(
                        "issued", row_id, issued.claim_ids, issued.expires_at.isoformat(),
                        rule_ids=decision.rule_ids,
                    )  # fmt: skip
                spec = proposal.passport_share
                held = s.scalars(
                    select(Passport).where(
                        Passport.id == (spec.passport_id if spec else ""),
                        Passport.user_id == user_id,
                    )
                ).first()
                if spec is None or held is None:
                    raise ExecutionRejected("passport_not_found")
                nonce = spec.nonce
                if nonce is None:  # chat share: the system issues the challenge for this audience
                    nonce = "h" + hashlib.sha256(proposal.idempotency_key.encode()).hexdigest()[:31]
                    s.add(
                        PassportChallenge(
                            nonce=nonce,
                            audience=spec.audience[:80],
                            issued_at=now,
                            issued_by="holder",
                        )
                    )
                else:  # the verifier's challenge must be live, for this audience, and unused
                    ch = s.get(PassportChallenge, nonce, with_for_update=True)
                    if (
                        ch is None
                        or ch.used_at is not None
                        or ch.audience != spec.audience[:80]
                        or now - ch.issued_at > timedelta(minutes=PASSPORT_CHALLENGE_MINUTES)
                    ):
                        raise ExecutionRejected("challenge_invalid", decision.rule_ids)
                pres = credential.present(
                    held.token,
                    list(held.disclosures),
                    list(spec.claim_ids),
                    audience=spec.audience,
                    nonce=nonce,
                    holder_key_hex=vault.passport_holder_key_hex(user_id, self.secrets_dir),
                    now=now,
                )
                s.add(
                    PassportPresentation(
                        id=row_id, user_id=user_id, passport_id=held.id, audience=spec.audience,
                        claim_ids=list(spec.claim_ids), presentation=pres, created_at=now,
                        idempotency_key=proposal.idempotency_key,
                    )
                )  # fmt: skip
                s.flush()
                self._append(
                    s, LedgerEventType.PASSPORT_SHARED, user_id, trace_id, session_id, now,
                    {
                        **common, "passport_id": held.id, "presentation_id": row_id,
                        "claim_ids": list(spec.claim_ids), "audience": spec.audience,
                    },
                    decision.policy_version_hash,
                )  # fmt: skip
                return PassportDone(
                    "shared", held.id, tuple(spec.claim_ids), held.expires_at.isoformat(),
                    spec.audience, pres, decision.rule_ids,
                )  # fmt: skip
        except ExecutionRejected:
            raise
        except IntegrityError as exc:
            raise ExecutionRejected("duplicate_idempotency_key") from exc

    def _run_request_contribution(
        self,
        proposal: ActionProposal,
        proposal_hash: str,
        grant_id: str,
        user_id: str,
        now: datetime,
        trace_id: str,
        session_id: str | None,
    ) -> ContributionRequested:
        """Store a request for money from a linked family member. It moves no money: the contributor
        decides in their own account, so a confirmed request can never pay anyone (INV-1).
        The policy
        (R-CONTRIBUTION) checks the family link and the per request limit again here; the link and
        the target account are re-read from the database, not trusted from the proposal."""
        spec, amount = proposal.contribution, proposal.amount
        if spec is None or amount is None:
            raise ExecutionRejected("incomplete_proposal")
        request_id = "cr_" + hashlib.sha256(proposal.idempotency_key.encode()).hexdigest()[:20]
        try:
            with Session(self.engine) as s, s.begin():
                s.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                    {"k": "user:" + user_id},
                )
                if s.get(ContributionRequest, request_id) is not None:
                    raise ExecutionRejected("idempotency_key_reuse")
                target = s.scalars(
                    select(Account).where(
                        Account.id == spec.target_account_id,
                        Account.user_id == user_id,
                        Account.status == "active",
                    )
                ).first()
                if target is None:
                    raise ExecutionRejected("account_not_allowed")
                if proposal.invoice_id is not None and (
                    s.scalars(
                        select(Invoice.id).where(
                            Invoice.id == proposal.invoice_id, Invoice.user_id == user_id
                        )
                    ).first()
                    is None
                ):
                    raise ExecutionRejected("invoice_mismatch")
                state = build_account_state(s, user_id, now, self.config)
                decision = evaluate_ablated(self.ablations, proposal, state, self.config, now)
                if decision.decision is Decision.DENY:
                    raise ExecutionRejected("policy_denied", decision.rule_ids)
                if self.ablations.has(Ablation.NO_GRANTS):  # evaluation only
                    grant = unbound_grant(user_id, proposal_hash, now)
                else:
                    try:
                        grant = consume_grant(
                            s, grant_id=grant_id, proposal_hash=proposal_hash, user_id=user_id,
                            minimum=required_level(decision), now=now,
                        )  # fmt: skip
                    except GrantError as exc:
                        raise ExecutionRejected(f"grant_{exc.code}", decision.rule_ids) from exc
                s.add(
                    ContributionRequest(
                        id=request_id, requester_user_id=user_id,
                        contributor_user_id=spec.contributor_user_id,
                        amount_minor=amount.value.amount_minor,
                        currency=amount.value.currency.value,
                        target_account_id=spec.target_account_id, invoice_id=proposal.invoice_id,
                        status="pending", idempotency_key=proposal.idempotency_key,
                        proposal_hash=proposal_hash, created_at=now,
                    )
                )  # fmt: skip
                s.flush()
                self._append(
                    s, LedgerEventType.EXECUTION, user_id, trace_id, session_id, now,
                    {
                        "proposal_hash": proposal_hash, "action": "request_contribution",
                        "request_id": request_id, "grant_id": grant.grant_id,
                        "grant_level": grant.level.value, "decision": decision.decision.value,
                        "rule_ids": list(decision.rule_ids),
                        "contributor_user_id": spec.contributor_user_id,
                        "amount_minor": amount.value.amount_minor,
                        "currency": amount.value.currency.value,
                        "invoice_id": proposal.invoice_id,
                    },
                    decision.policy_version_hash,
                )  # fmt: skip
        except ExecutionRejected:
            raise
        except IntegrityError as exc:
            raise ExecutionRejected("duplicate_idempotency_key") from exc
        return ContributionRequested(
            request_id=request_id, contributor_user_id=spec.contributor_user_id,
            amount_minor=amount.value.amount_minor, currency=amount.value.currency.value,
            rule_ids=decision.rule_ids,
        )  # fmt: skip

    def _replay(
        self,
        s: Session,
        earlier: Transfer,
        proposal: ActionProposal,
        proposal_hash: str,
        user_id: str,
        grant_id: str,
        now: datetime,
        trace_id: str,
        session_id: str | None,
    ) -> ExecutionResult:
        """A reused key returns the earlier result only for the same user, the same exact terms and
        the grant that was spent on that execution (crash recovery after the commit). Anything else
        is rejected, so repeating a key never helps an attacker."""
        spent = s.scalars(
            select(ConfirmationGrantRow).where(ConfirmationGrantRow.grant_id == grant_id)
        ).first()
        if (
            earlier.user_id != user_id
            or earlier.proposal_hash != proposal_hash
            or spent is None
            or not spent.used
            or spent.user_id != user_id
            or spent.proposal_hash != proposal_hash
        ):
            raise ExecutionRejected("idempotency_key_reuse")
        balance = s.scalar(select(Account.balance_minor).where(Account.id == earlier.from_account))
        self._append(
            s, LedgerEventType.REJECTION, user_id, trace_id, session_id, now,
            {"proposal_hash": proposal_hash, "reason": "idempotent_replay",
             "transfer_id": earlier.id},
            self.config.version_hash,
        )  # fmt: skip
        return ExecutionResult(
            transfer_id=earlier.id, amount_minor=earlier.amount_minor, currency=earlier.currency,
            payee_id=earlier.payee_id, from_account=earlier.from_account,
            balance_after_minor=int(balance or 0), replayed=True,
        )  # fmt: skip

    def _append(
        self,
        s: Session,
        event: LedgerEventType,
        user_id: str,
        trace_id: str,
        session_id: str | None,
        now: datetime,
        data: dict[str, Any],
        policy_version_hash: str,
    ) -> None:
        self.ledger.append(
            s, trace_id=trace_id, actor=LedgerActor.SYSTEM, event_type=event, user_id=user_id,
            session_id=session_id, data=data, policy_version_hash=policy_version_hash, now=now,
        )  # fmt: skip

    def _record_failure(
        self,
        proposal: ActionProposal,
        user_id: str,
        exc: ExecutionRejected,
        now: datetime,
        trace_id: str,
        session_id: str | None,
    ) -> None:
        """Append the failure in its own transaction, and count high risk failures (INV-11)."""
        try:
            with Session(self.engine) as s, s.begin():
                locked = None
                if exc.reason.removeprefix("grant_") in _GRANT_FAILURES:
                    locked = record_event(s, user_id, "failed_high_risk", now, self.config)
                elif exc.reason == "policy_denied" and HIGH_RISK_RULES.intersection(exc.rule_ids):
                    docs = tuple(proposal.derived_from_docs)
                    if docs:  # documents together count as at most one attempt (ADR 0006)
                        locked = record_policy_denial_docs(s, user_id, docs, now, self.config)
                    else:
                        locked = record_event(s, user_id, "denied", now, self.config)
                self._append(
                    s, LedgerEventType.EXECUTION_FAILED, user_id, trace_id, session_id, now,
                    {"proposal_hash": proposal.canonical_hash(), "reason": exc.reason,
                     "rule_ids": list(exc.rule_ids), "action": proposal.action.value},
                    self.config.version_hash,
                )  # fmt: skip
                if locked is not None:
                    self._append(
                        s, LedgerEventType.SAFETY_LOCK, user_id, trace_id, session_id, now,
                        {"action": "locked", "reason": locked}, self.config.version_hash,
                    )  # fmt: skip
        except Exception as inner:  # the action already failed; never mask it
            log.error("failure_record_failed", error=type(inner).__name__)
