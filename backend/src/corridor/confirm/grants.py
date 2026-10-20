"""Server side confirmation grants (WYSIWYS, docs/spec/modules.md 5.8, INV-5).

A grant is bound to the SHA-256 of one exact proposal and one user, lives exactly 120 seconds and is
single use. ``consume_grant`` is one atomic ``UPDATE ... WHERE ... RETURNING`` so two concurrent
executions cannot both use it. Every failure raises ``GrantError`` with a short code and nothing is
ever allowed by default (INV-10).
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from corridor.contracts.confirm import GRANT_TTL_SECONDS, ConfirmationGrant, ConfirmationLevel
from corridor.sandbox.models import ConfirmationGrantRow

_LEVEL_RANK = {ConfirmationLevel.CONFIRM: 1, ConfirmationLevel.MFA: 2}


class GrantError(Exception):
    """A grant could not be issued or used. ``code`` says why (never shown as a success)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def level_satisfies(have: ConfirmationLevel, need: ConfirmationLevel) -> bool:
    """True if a grant of level ``have`` is enough for a decision that needs ``need``."""
    return _LEVEL_RANK[have] >= _LEVEL_RANK[need]


def _to_model(row: ConfirmationGrantRow) -> ConfirmationGrant:
    return ConfirmationGrant(
        grant_id=row.grant_id,
        proposal_hash=row.proposal_hash,
        user_id=row.user_id,
        level=ConfirmationLevel(row.level),
        issued_at=row.issued_at,
        expires_at=row.expires_at,
        used=row.used,
    )


def issue_grant(
    session: Session,
    *,
    proposal_hash: str,
    user_id: str,
    level: ConfirmationLevel,
    required: ConfirmationLevel,
    issued_at: datetime,
    now: datetime,
) -> ConfirmationGrant:
    """Mint a grant for exactly ``proposal_hash``.

    Refuses a grant issued in the future and a level below what the freshly evaluated policy
    decision requires (``required``)."""
    if issued_at > now:
        raise GrantError("issued_in_future")
    if not level_satisfies(level, required):
        raise GrantError("level_too_low")
    grant = ConfirmationGrant(
        grant_id=f"grant_{uuid.uuid4().hex}",
        proposal_hash=proposal_hash,
        user_id=user_id,
        level=level,
        issued_at=issued_at,
        expires_at=issued_at + timedelta(seconds=GRANT_TTL_SECONDS),
    )
    session.add(
        ConfirmationGrantRow(
            grant_id=grant.grant_id, proposal_hash=proposal_hash, user_id=user_id,
            level=level.value, issued_at=grant.issued_at, expires_at=grant.expires_at, used=False,
        )
    )  # fmt: skip
    session.flush()
    return grant


def consume_grant(
    session: Session,
    *,
    grant_id: str,
    proposal_hash: str,
    user_id: str,
    minimum: ConfirmationLevel,
    now: datetime,
) -> ConfirmationGrant:
    """Atomically use a grant: right hash, right user, not expired, not used, level high enough.

    Returns the grant as consumed (``used=True``). Raises ``GrantError`` with the first reason that
    applies: ``not_found``, ``wrong_user``, ``hash_mismatch``, ``already_used``, ``expired``,
    ``level_too_low``."""
    levels = [lv.value for lv in ConfirmationLevel if level_satisfies(lv, minimum)]
    row = session.execute(
        update(ConfirmationGrantRow)
        .where(
            ConfirmationGrantRow.grant_id == grant_id,
            ConfirmationGrantRow.used.is_(False),
            ConfirmationGrantRow.proposal_hash == proposal_hash,
            ConfirmationGrantRow.user_id == user_id,
            ConfirmationGrantRow.issued_at <= now,
            ConfirmationGrantRow.expires_at > now,
            ConfirmationGrantRow.level.in_(levels),
        )
        .values(used=True, used_at=now)
        .returning(ConfirmationGrantRow)
    ).scalar_one_or_none()
    if row is not None:
        return _to_model(row)
    found = session.scalars(
        select(ConfirmationGrantRow).where(ConfirmationGrantRow.grant_id == grant_id)
    ).first()
    if found is None:
        raise GrantError("not_found")
    if found.user_id != user_id:
        raise GrantError("wrong_user")
    if found.proposal_hash != proposal_hash:
        raise GrantError("hash_mismatch")
    if found.used:
        raise GrantError("already_used")
    if found.expires_at <= now or found.issued_at > now:
        raise GrantError("expired")
    raise GrantError("level_too_low")
