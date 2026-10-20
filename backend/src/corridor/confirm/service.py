"""Confirmation, MFA and unlock orchestration with ledger entries (modules 5.8, INV-5, INV-8).

``confirm`` turns "the user clicked confirm on the card with hash H" into a ``ConfirmationGrant``,
after checking the lock and, when policy requires MFA, a valid TOTP code. Every attempt and result
is appended to the ledger in the same transaction as the state change. Failures are returned as
an outcome (not raised) so the failure record and the lock are committed by the caller.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from corridor.confirm.grants import GrantError, issue_grant
from corridor.confirm.lock import (
    count_events,
    is_locked,
    unlock_user,
    window_start,
)
from corridor.confirm.totp import verify_totp
from corridor.contracts.confirm import ConfirmationGrant, ConfirmationLevel
from corridor.contracts.ledger import LedgerActor, LedgerEventType
from corridor.ledger import Ledger
from corridor.policy.config import PolicyConfig


@dataclass(frozen=True)
class ConfirmOutcome:
    """Result of a confirm or unlock attempt."""

    ok: bool
    grant: ConfirmationGrant | None = None
    reason: str | None = None
    attempts_left: int | None = None
    locked: bool = False


@dataclass(frozen=True)
class _Ctx:
    user_id: str
    trace_id: str
    session_id: str | None
    now: datetime


class ConfirmationService:
    """Mints grants and handles MFA and unlock. Holds no state: everything is in the database."""

    def __init__(
        self, config: PolicyConfig, ledger: Ledger, secrets_dir: Path | None = None
    ) -> None:
        self.config, self.ledger, self.secrets_dir = config, ledger, secrets_dir

    def _log(
        self, session: Session, ctx: "_Ctx", event: LedgerEventType, data: dict[str, object]
    ) -> None:
        self.ledger.append(
            session, trace_id=ctx.trace_id, actor=LedgerActor.USER, event_type=event,
            user_id=ctx.user_id, session_id=ctx.session_id, data=data,
            policy_version_hash=self.config.version_hash, now=ctx.now,
        )  # fmt: skip

    @staticmethod
    def _serialise(session: Session, user_id: str) -> None:
        """One user's code attempts are counted one at a time, so the budget is strict."""
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": "user:" + user_id}
        )

    def _attempts_left(self, session: Session, user_id: str, now: datetime) -> int:
        used = count_events(
            session, user_id, ("totp_failed",), window_start(session, user_id, now, self.config)
        )
        return max(self.config.lock.totp_failures - used, 0)

    def confirm(
        self,
        session: Session,
        *,
        user_id: str,
        proposal_hash: str,
        required: ConfirmationLevel,
        totp: str | None,
        now: datetime,
        trace_id: str,
        session_id: str | None,
    ) -> ConfirmOutcome:
        """Mint a grant for ``proposal_hash`` if the lock, MFA and level rules allow it."""
        self._serialise(session, user_id)
        ctx = _Ctx(user_id, trace_id, session_id, now)

        def reject(
            reason: str, attempts_left: int | None = None, locked: bool = False
        ) -> ConfirmOutcome:
            self._log(
                session,
                ctx,
                LedgerEventType.CONFIRMATION_REJECTED,
                {"proposal_hash": proposal_hash, "reason": reason, "required": required.value},
            )
            return ConfirmOutcome(False, None, reason, attempts_left, locked)

        if is_locked(session, user_id):
            return reject("locked", locked=True)
        level = ConfirmationLevel.CONFIRM
        if required is ConfirmationLevel.MFA:
            if not totp:
                return reject("mfa_required")
            ok, lock_reason = verify_totp(
                session, user_id, totp, now, self.config, self.secrets_dir
            )
            self._log(
                session,
                ctx,
                LedgerEventType.MFA_ATTEMPT,
                {"proposal_hash": proposal_hash, "ok": ok},
            )
            if not ok:
                if lock_reason is not None:
                    self._log(
                        session,
                        ctx,
                        LedgerEventType.SAFETY_LOCK,
                        {"action": "locked", "reason": lock_reason},
                    )
                left = self._attempts_left(session, user_id, now)
                return reject("mfa_failed", left, lock_reason is not None)
            level = ConfirmationLevel.MFA
        try:
            grant = issue_grant(
                session, proposal_hash=proposal_hash, user_id=user_id, level=level,
                required=required, issued_at=now, now=now,
            )  # fmt: skip
        except GrantError as exc:
            return reject(exc.code)
        self._log(
            session,
            ctx,
            LedgerEventType.CONFIRMATION_GRANTED,
            {"proposal_hash": proposal_hash, "grant_id": grant.grant_id, "level": level.value},
        )
        return ConfirmOutcome(True, grant)

    def unlock(
        self,
        session: Session,
        *,
        user_id: str,
        totp: str | None,
        now: datetime,
        trace_id: str,
        session_id: str | None,
    ) -> ConfirmOutcome:
        """Lift the safety lock after a valid TOTP code (MFA re-verification, INV-11)."""
        self._serialise(session, user_id)
        ctx = _Ctx(user_id, trace_id, session_id, now)
        if not is_locked(session, user_id):
            return ConfirmOutcome(True, reason="not_locked")
        if self._attempts_left(session, user_id, now) == 0:  # no unlimited guessing while locked
            self._log(
                session,
                ctx,
                LedgerEventType.MFA_ATTEMPT,
                {"purpose": "unlock", "ok": False, "cooldown": True},
            )
            return ConfirmOutcome(False, reason="unlock_cooldown", attempts_left=0, locked=True)
        ok, _ = verify_totp(session, user_id, totp, now, self.config, self.secrets_dir)
        self._log(session, ctx, LedgerEventType.MFA_ATTEMPT, {"purpose": "unlock", "ok": ok})
        if not ok:
            return ConfirmOutcome(False, reason="mfa_failed", locked=True)
        unlock_user(session, user_id, now)
        self._log(session, ctx, LedgerEventType.SAFETY_LOCK, {"action": "unlocked"})
        return ConfirmOutcome(True)
