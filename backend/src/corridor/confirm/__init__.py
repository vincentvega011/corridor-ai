"""Confirmation grants, TOTP, safety lock and the only creator of USER_CONFIRMED (modules 5.8)."""

from corridor.confirm.grants import GrantError, consume_grant, issue_grant, level_satisfies
from corridor.confirm.lock import (
    is_locked,
    lock_user,
    record_event,
    record_policy_denial,
    unlock_user,
)
from corridor.confirm.service import ConfirmationService, ConfirmOutcome
from corridor.confirm.totp import current_code, verify_totp
from corridor.confirm.upgrade import UpgradeError, confirm_values

__all__ = [
    "ConfirmOutcome",
    "ConfirmationService",
    "GrantError",
    "UpgradeError",
    "confirm_values",
    "consume_grant",
    "current_code",
    "is_locked",
    "issue_grant",
    "level_satisfies",
    "lock_user",
    "record_event",
    "record_policy_denial",
    "unlock_user",
    "verify_totp",
]
