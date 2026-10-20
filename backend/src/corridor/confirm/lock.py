"""Safety lock (INV-11): persisted, and lifted only by MFA re-verification.

Triggers (limits in ``policy.yaml``): three denied or failed high risk attempts in the window, one
critical risk event, or five failed TOTP codes in the window. The state lives in the database, so a
restart, a new session or a new process cannot clear it.
"""

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from corridor.contracts.policy import Decision, PolicyDecision
from corridor.policy.config import PolicyConfig
from corridor.policy.rules import HIGH_RISK_RULES
from corridor.sandbox.models import SafetyLock, SecurityEvent

DENIAL_KINDS = ("denied", "failed_high_risk")


def is_locked(session: Session, user_id: str) -> bool:
    """True if outgoing actions are locked for ``user_id``."""
    row = session.get(SafetyLock, user_id)
    return bool(row and row.locked)


def lock_user(session: Session, user_id: str, reason: str, now: datetime) -> None:
    """Lock the user (idempotent). The reason is one of a few fixed codes."""
    stmt = insert(SafetyLock).values(
        user_id=user_id, locked=True, reason=reason, locked_at=now, unlocked_at=None
    )
    session.execute(  # an upsert: two concurrent lockers cannot collide on the primary key
        stmt.on_conflict_do_update(
            index_elements=[SafetyLock.user_id],
            set_={"locked": True, "reason": reason, "locked_at": now, "unlocked_at": None},
            where=SafetyLock.locked.is_(False),
        )
    )
    session.expire_all()


def unlock_user(session: Session, user_id: str, now: datetime) -> None:
    """Lift the lock. Callers must have verified a TOTP code first (see ``ConfirmationService``)."""
    row = session.get(SafetyLock, user_id)
    if row is not None and row.locked:
        row.locked, row.unlocked_at = False, now
        session.flush()


def count_events(session: Session, user_id: str, kinds: tuple[str, ...], since: datetime) -> int:
    """Number of security events of the given kinds for the user after ``since``."""
    return int(
        session.scalar(
            select(func.count())
            .select_from(SecurityEvent)
            .where(
                SecurityEvent.user_id == user_id,
                SecurityEvent.kind.in_(kinds),
                SecurityEvent.ts > since,
            )
        )
        or 0
    )


def window_start(session: Session, user_id: str, now: datetime, config: PolicyConfig) -> datetime:
    """Start of the counting window: the rolling window, but never before the last unlock."""
    since = now - timedelta(minutes=config.lock.window_minutes)
    lock_row = session.get(SafetyLock, user_id)
    if lock_row is not None and lock_row.unlocked_at is not None:
        since = max(since, lock_row.unlocked_at)
    return since


def record_event(
    session: Session,
    user_id: str,
    kind: str,
    now: datetime,
    config: PolicyConfig,
    ref: str | None = None,
) -> str | None:
    """Record a security event and lock if a trigger fires; returns the reason if it just locked.

    Events before the last unlock do not count, so unlocking is not undone by old failures."""
    session.add(SecurityEvent(user_id=user_id, ts=now, kind=kind, ref=ref))
    session.flush()
    if is_locked(session, user_id):
        return None
    reason = None
    if kind == "critical":
        reason = "critical_risk"
    else:
        since = window_start(session, user_id, now, config)

        # Denials caused by documents together count as at most one attempt (ADR 0006): a stranger
        # cannot lock a user by sending them poisoned documents, only the user's own repeated
        # attempts (wrong hashes, wrong codes, attack rule denials on proposals they typed) can.
        own = count_events(session, user_id, DENIAL_KINDS, since)
        from_documents = min(count_events(session, user_id, ("doc_denied",), since), 1)
        if own + from_documents >= config.lock.denied_attempts:
            reason = "denied_attempts"
        elif count_events(session, user_id, ("totp_failed",), since) >= config.lock.totp_failures:
            reason = "totp_failures"
    if reason is not None:
        lock_user(session, user_id, reason, now)
    return reason


def record_policy_denial(
    session: Session,
    user_id: str,
    decision: PolicyDecision,
    now: datetime,
    config: PolicyConfig,
    doc_hashes: tuple[str, ...] = (),
) -> str | None:
    """Count a high risk denial toward the lock (INV-11). Returns the lock reason if it just locked.

    A critical risk score locks at once; a denial by an attack related rule counts as one attempt;
    an ordinary denial (balance, limit) is ignored. If the proposal came from documents
    (``doc_hashes``) the denial is recorded once per document hash and the documents together
    count as at most one attempt, so third parties cannot lock a user with poisoned documents."""
    if decision.decision is not Decision.DENY:
        return None
    if "R-RISK.risk_critical" in decision.reasons and not doc_hashes:
        return record_event(session, user_id, "critical", now, config)
    # a critical score on a proposal built from documents is counted like any other document denial
    # (once per document, at most one attempt): a poisoned document must not lock its victim
    # (ADR 0006)
    if HIGH_RISK_RULES.intersection(decision.rule_ids):
        if doc_hashes:
            return record_policy_denial_docs(session, user_id, doc_hashes, now, config)
        return record_event(session, user_id, "denied", now, config)
    return None


def record_policy_denial_docs(
    session: Session, user_id: str, doc_hashes: tuple[str, ...], now: datetime, config: PolicyConfig
) -> str | None:
    """Record an attack rule denial that came from documents: once per hash, at most one attempt."""
    seen = set(
        session.scalars(
            select(SecurityEvent.ref).where(
                SecurityEvent.user_id == user_id,
                SecurityEvent.kind == "doc_denied",
                SecurityEvent.ref.in_(doc_hashes),
            )
        )
    )
    fresh = [h for h in doc_hashes if h not in seen]
    return (
        record_event(session, user_id, "doc_denied", now, config, ref=fresh[0]) if fresh else None
    )
