"""verify_payee: the only way a payee becomes verified (ADR 0012, INV-1, INV-4).

A bank officer, never the customer and never a model, can verify a payee, and only when the
simulated Confirmation of Payee registry holds a holder name for the payee's account that matches
the payee's name. The action is not a tool any LLM can call and is not an ``ActionType`` the agent
can propose: it is reachable only through the officer API. Every attempt, including a refusal, is
written to the hash chained ledger with the officer id and reason (INV-8). Verification sets
``verified_at``; it does not change the payee's provenance (an account that came from a document
stays untrusted, so MFA still applies), it ends the unverified cap of R-PAYEE-COOLOFF.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from corridor.contracts.ledger import LedgerActor, LedgerEventType
from corridor.ledger import Ledger
from corridor.policy.config import PolicyConfig
from corridor.sandbox.cop import names_match, registered_holder
from corridor.sandbox.models import Payee, VerificationRequest


class VerificationRefused(Exception):
    """The payee was not verified. ``reason`` is a short machine readable code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Verified:
    """A payee that was verified, with who did it and when."""

    payee_id: str
    user_id: str
    officer_id: str
    verified_at: datetime


def _append(
    led: Ledger, s: Session, config: PolicyConfig, event: LedgerEventType, owner: str | None,
    trace_id: str, now: datetime, data: dict[str, Any],
) -> None:  # fmt: skip
    led.append(
        s, trace_id=trace_id, actor=LedgerActor.SYSTEM, event_type=event, user_id=owner,
        session_id=None, data=data, policy_version_hash=config.version_hash, now=now,
    )  # fmt: skip


class VerificationRequestRefused(Exception):
    """A customer's verification request was not created. ``reason`` is a short code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class VerificationRequested:
    """A pending request for the bank to verify one payee."""

    request_id: str
    payee_id: str
    status: str


def request_verification(
    engine: Engine, config: PolicyConfig, *, user_id: str, payee_id: str, now: datetime,
    trace_id: str, ledger: Ledger | None = None,
) -> VerificationRequested:  # fmt: skip
    """Ask the bank to verify one of the caller's own payees (ADR 0018).

    This changes nothing about the payee: it only puts the payee in the officer queue. The
    customer cannot
    verify anything (INV-1): ``verify_payee`` stays officer only and needs a Confirmation of
    Payee match.
    One open request per payee; the attempt is ledgered (INV-8)."""
    led = ledger or Ledger()
    with Session(engine) as s, s.begin():
        payee = s.scalars(
            select(Payee).where(Payee.id == payee_id, Payee.user_id == user_id).with_for_update()
        ).first()
        if payee is None:
            raise VerificationRequestRefused("payee_not_found")  # also for someone else's payee
        if payee.verified_at is not None:
            raise VerificationRequestRefused("already_verified")
        open_one = s.scalars(
            select(VerificationRequest).where(
                VerificationRequest.payee_id == payee_id, VerificationRequest.status == "pending"
            )
        ).first()
        if open_one is not None:
            return VerificationRequested(open_one.id, payee_id, "pending")
        rid = "vr_" + hashlib.sha256(f"{payee_id}|{now.isoformat()}".encode()).hexdigest()[:20]
        s.add(
            VerificationRequest(
                id=rid, user_id=user_id, payee_id=payee_id, status="pending", created_at=now
            )
        )
        s.flush()
        _append(
            led, s, config, LedgerEventType.VERIFICATION_REQUESTED, user_id, trace_id, now,
            {"action": "request_verification", "payee_id": payee_id, "request_id": rid,
             "payee_provenance": payee.provenance},
        )  # fmt: skip
    return VerificationRequested(rid, payee_id, "pending")


def _resolve_requests(s: Session, payee_id: str, status: str, officer: str, now: datetime,
                      refusal: str | None) -> None:  # fmt: skip
    for r in s.scalars(
        select(VerificationRequest).where(
            VerificationRequest.payee_id == payee_id, VerificationRequest.status == "pending"
        )
    ):
        r.status, r.resolved_at, r.officer_id, r.refusal = status, now, officer, refusal


VERIFICATION_METHODS = (
    "callback_independent_number",  # called back on a number not taken from the customer's papers
    "in_branch_check",  # the customer came to a branch with the lease or invoice
    "university_housing_office_list",  # the payee is on the university housing office's list
)
"""Out-of-band checks an officer may record (ADR 0019). A Confirmation of Payee match alone is
not enough:
a scammer's account is registered under the name the scammer gave."""


def verify_payee(
    engine: Engine, config: PolicyConfig, *, officer_id: str, payee_id: str, method: str,
    reference: str, now: datetime, trace_id: str, ledger: Ledger | None = None,
) -> Verified:  # fmt: skip
    """Verify ``payee_id`` as ``officer_id``, or raise ``VerificationRefused`` (and ledger it).

    Needs all three: the Confirmation of Payee result (computed here, must be a match), an
    out-of-band
    ``method`` from ``VERIFICATION_METHODS`` and a free text ``reference`` (what the officer
    checked, for
    example who was called back). Method, reference and the CoP result are all ledgered with
    the officer id."""
    led = ledger or Ledger()
    why = reference.strip()[:200]
    refusal: str | None = None
    owner = ""
    base = {
        "action": "verify_payee", "payee_id": payee_id, "officer_id": officer_id, "reason": why,
        "reference": why, "method": method,
    }  # fmt: skip
    with Session(engine) as s, s.begin():
        payee = s.scalars(select(Payee).where(Payee.id == payee_id).with_for_update()).first()
        if payee is None:
            refusal = "payee_not_found"
        else:
            owner = payee.user_id
            holder = registered_holder(s, payee.account_no_hash)
            cop = (
                "no_record"
                if holder is None
                else "match"
                if names_match(payee.display_name, holder)
                else "mismatch"
            )
            base["cop"] = cop
            if payee.verified_at is not None:
                refusal = "already_verified"
            elif not officer_id:
                refusal = "officer_and_reason_required"
            elif method not in VERIFICATION_METHODS:
                refusal = "method_required"
            elif len(why) < 3:
                refusal = "reference_required"
            elif cop == "no_record":
                refusal = "cop_no_record"
            elif cop == "mismatch":
                refusal = "cop_mismatch"
            else:
                payee.verified_at = now
                _resolve_requests(s, payee_id, "verified", officer_id, now, None)
                data = {**base, "payee_provenance": payee.provenance}
                _append(led, s, config, LedgerEventType.EXECUTION, owner, trace_id, now, data)
                return Verified(payee_id, owner, officer_id, now)
    with Session(engine) as s, s.begin():  # the refusal is recorded in its own transaction
        if refusal not in (
            None,
            "payee_not_found",
            "officer_and_reason_required",
            "already_verified",
            "method_required",
            "reference_required",
        ):
            _resolve_requests(s, payee_id, "refused", officer_id, now, refusal)
        data = {**base, "refused": refusal}
        _append(led, s, config, LedgerEventType.REJECTION, owner or None, trace_id, now, data)
    raise VerificationRefused(refusal or "refused")
