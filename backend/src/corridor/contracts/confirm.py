"""Confirmation grants (WYSIWYS, docs/spec/modules.md 5.8).

Security reason (INV-5): a grant is bound to the SHA-256 of one exact proposal, expires
120 seconds after issue, and is single use. The model is frozen; marking a grant used
produces a new copy inside ``corridor.confirm``, which owns storage and the single-use
check. The expiry rule is enforced here at construction so a grant with a longer life
cannot even be represented.
"""

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from corridor.contracts.scalars import IdStr, Sha256Hex

GRANT_TTL_SECONDS = 120


class ConfirmationLevel(StrEnum):
    """CONFIRM is a tap on the card; MFA additionally requires a valid TOTP code."""

    CONFIRM = "confirm"
    MFA = "mfa"


class ConfirmationGrant(BaseModel):
    """Proof that a user approved one exact proposal hash."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    grant_id: IdStr
    proposal_hash: Sha256Hex
    user_id: IdStr
    level: ConfirmationLevel
    issued_at: datetime
    expires_at: datetime
    used: bool = False

    @model_validator(mode="after")
    def _ttl(self) -> Self:
        for ts in (self.issued_at, self.expires_at):
            if ts.tzinfo is None or ts.utcoffset() is None:
                raise ValueError("grant timestamps must be timezone aware")
        if self.expires_at - self.issued_at != timedelta(seconds=GRANT_TTL_SECONDS):
            raise ValueError(f"grant must expire exactly {GRANT_TTL_SECONDS} seconds after issue")
        return self

    def is_expired(self, now: datetime) -> bool:
        """True at or after ``expires_at``. A naive ``now`` is rejected (fail closed)."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone aware")
        return now.astimezone(UTC) >= self.expires_at.astimezone(UTC)
