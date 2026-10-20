"""TOTP (RFC 6238) verification with replay protection and failure counting (spec/modules.md 5.8).

Seeds come from ``secrets/mfa/<user>.seed`` and never enter a prompt or a log. A code is accepted
within a window of one time step either side, and each accepted time step can be used once, so an
observed code cannot be replayed. A wrong code is recorded as a security event and can trigger the
safety lock (INV-11).
"""

import re
from datetime import datetime
from pathlib import Path

import pyotp
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from corridor.confirm.lock import record_event
from corridor.policy.config import PolicyConfig
from corridor.sandbox import vault
from corridor.sandbox.models import TotpStep

STEP_SECONDS = 30
_CODE = re.compile(r"^[0-9]{6}$")


def current_code(user_id: str, now: datetime, secrets_dir: Path | None = None) -> str:
    """The valid code at ``now``. For the dev helper, the sandbox authenticator panel (dev or demo,
    loopback only, never a production build) and tests."""
    return pyotp.TOTP(vault.mfa_seed(user_id, secrets_dir)).at(now)


def verify_totp(
    session: Session,
    user_id: str,
    code: str | None,
    now: datetime,
    config: PolicyConfig,
    secrets_dir: Path | None = None,
) -> tuple[bool, str | None]:
    """Check a code. Returns ``(ok, lock_reason)``; the reason is set if this failure locked."""
    if code is not None and _CODE.match(code):
        totp = pyotp.TOTP(vault.mfa_seed(user_id, secrets_dir))
        base = int(now.timestamp()) // STEP_SECONDS
        for step in (base, base - 1, base + 1):
            if totp.at(step * STEP_SECONDS) == code:
                inserted = session.execute(
                    insert(TotpStep)
                    .values(user_id=user_id, step=step)
                    .on_conflict_do_nothing()
                    .returning(TotpStep.step)
                ).first()
                if inserted is not None:
                    return True, None
                break  # the code is right but already used
    return False, record_event(session, user_id, "totp_failed", now, config)
