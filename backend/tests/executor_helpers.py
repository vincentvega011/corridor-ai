"""Build executable proposals and grants against the seeded sandbox."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from corridor.confirm import issue_grant
from corridor.contracts import (
    ActionProposal,
    ActionType,
    CurrencyCode,
    Money,
    Provenance,
    Tainted,
)
from corridor.contracts.confirm import ConfirmationGrant, ConfirmationLevel
from corridor.executor import Executor
from corridor.policy import load_policy_config

CFG = load_policy_config()
NOW = datetime.now(UTC)
QAR = CurrencyCode.QAR


def pay_landlord(
    amount_minor: int = 5_000, *, key: str | None = None, user: str = "usr_ramesh",
    payee: str = "pay_ramesh_landlord", account: str = "acc_ramesh_qar",
    action: ActionType = ActionType.TRANSFER, invoice_id: str | None = None,
    currency: CurrencyCode = QAR, amount_prov: Provenance = Provenance.USER_TYPED, **over: Any,
) -> ActionProposal:  # fmt: skip
    """A domestic QAR payment (QAR 50.00 by default: NPR ~1,900, so only a confirm tap)."""
    base: dict[str, Any] = {
        "proposal_id": f"prop_{uuid.uuid4().hex[:8]}", "action": action, "user_id": user,
        "source_account_id": Tainted[str](value=account, provenance=Provenance.SYSTEM),
        "payee_id": Tainted[str](value=payee, provenance=Provenance.SYSTEM),
        "amount": Tainted[Money](
            value=Money(amount_minor=amount_minor, currency=currency), provenance=amount_prov
        ),
        "idempotency_key": key or f"idem-{uuid.uuid4().hex}", "invoice_id": invoice_id,
    }  # fmt: skip
    base.update(over)
    return ActionProposal(**base)


def grant_for(
    engine: Engine, proposal: ActionProposal, level: ConfirmationLevel = ConfirmationLevel.CONFIRM,
    *, user: str | None = None, now: datetime | None = None,
) -> ConfirmationGrant:  # fmt: skip
    t = now or NOW
    with Session(engine) as s, s.begin():
        return issue_grant(
            s, proposal_hash=proposal.canonical_hash(), user_id=user or proposal.user_id,
            level=level, required=ConfirmationLevel.CONFIRM, issued_at=t, now=t,
        )  # fmt: skip


def run(
    ex: Executor,
    proposal: ActionProposal,
    grant: ConfirmationGrant,
    *,
    user: str | None = None,
    now: datetime | None = None,
):  # type: ignore[no-untyped-def]
    return ex.execute(
        proposal, grant.grant_id, user_id=user or proposal.user_id, now=now or NOW,
        trace_id="trace-test", session_id="sess-test",
    )  # fmt: skip


def balance(engine: Engine, account: str) -> int:
    with engine.connect() as c:
        return int(
            c.execute(
                text("SELECT balance_minor FROM accounts WHERE id=:a"), {"a": account}
            ).scalar_one()
        )


def count(engine: Engine, table: str, where: str = "true") -> int:
    with engine.connect() as c:
        return int(c.execute(text(f"SELECT count(*) FROM {table} WHERE {where}")).scalar_one())
