"""Builders for policy tests: a pure AccountState and proposals of every shape."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from corridor.contracts import (
    ActionProposal,
    ActionType,
    CurrencyCode,
    Money,
    Provenance,
    Tainted,
)
from corridor.contracts.action import (
    BudgetSpec,
    ContributionSpec,
    NewPayeeSpec,
    PassportShareSpec,
    ScheduleSpec,
)
from corridor.contracts.risk import RiskAssessment, RiskLevel
from corridor.policy import AccountInfo, AccountState, PayeeInfo, load_policy_config

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
CFG = load_policy_config()
AUD, NPR = CurrencyCode.AUD, CurrencyCode.NPR
SYSTEM, TYPED = Provenance.SYSTEM, Provenance.USER_TYPED


def tainted(value: Any, prov: Provenance = SYSTEM) -> Tainted[Any]:
    return Tainted[Any](value=value, provenance=prov)


def make_state(**over: Any) -> AccountState:
    """User u1 with an AUD account (AUD 10,000.00), a verified NPR payee and AUD to NPR corridor."""
    base: dict[str, Any] = {
        "user_id": "u1",
        "accounts": {"acc1": AccountInfo(currency=AUD, balance_minor=1_000_000)},
        "payees": {
            "payee1": PayeeInfo(
                currency=AUD, verified=True, created_at=NOW - timedelta(days=30), provenance=SYSTEM
            ),
            "payee_npr": PayeeInfo(
                currency=NPR, verified=True, created_at=NOW - timedelta(days=30), provenance=SYSTEM
            ),
        },
        "invoices": {},
        "fx_to_npr": {NPR: Decimal(1), AUD: Decimal("90")},
        "supported_pairs": frozenset({"AUD/NPR"}),
        "convert_pairs": frozenset({"AUD/NPR", "NPR/AUD"}),
    }
    base.update(over)
    return AccountState(**base)


def make_transfer(
    amount_minor: int = 5_000,
    *,
    payee: str = "payee1",
    amount_prov: Provenance = TYPED,
    payee_prov: Provenance = SYSTEM,
    account_prov: Provenance = SYSTEM,
    action: ActionType = ActionType.TRANSFER,
    **over: Any,
) -> ActionProposal:
    """A domestic AUD transfer: AUD 50.00 by default (NPR 4,500 equivalent, below the low limit)."""
    base: dict[str, Any] = {
        "proposal_id": "p1",
        "action": action,
        "user_id": "u1",
        "source_account_id": tainted("acc1", account_prov),
        "payee_id": tainted(payee, payee_prov),
        "amount": Tainted[Money](
            value=Money(amount_minor=amount_minor, currency=AUD), provenance=amount_prov
        ),
        "idempotency_key": "idem-1",
    }
    base.update(over)
    return ActionProposal(**base)


def make_other(action: ActionType, **over: Any) -> ActionProposal:
    """A proposal of a non money moving kind."""
    base: dict[str, Any] = {
        "proposal_id": "p2",
        "action": action,
        "user_id": "u1",
        "source_account_id": tainted("acc1"),
        "idempotency_key": "idem-2",
    }
    if action is ActionType.CANCEL_SCHEDULE:
        base["schedule_id"] = "sch1"
    if action is ActionType.SET_BUDGET:
        base["budget"] = BudgetSpec(category="groceries", limit=Money(amount_minor=1, currency=AUD))
    if action is ActionType.ADD_PAYEE:
        base["new_payee"] = NewPayeeSpec(
            display_name=tainted("Sam", TYPED), region=tainted("AU", TYPED),
            currency=AUD, account_no=tainted("123456", TYPED),
        )  # fmt: skip
    if action is ActionType.REQUEST_CONTRIBUTION:
        base.update(
            amount=Tainted[Money](value=Money(amount_minor=100, currency=AUD), provenance=TYPED),
            contribution=ContributionSpec(contributor_user_id="u2", target_account_id="acc1"),
        )
    if action is ActionType.SHARE_PASSPORT:
        base["passport_share"] = PassportShareSpec(
            passport_id="pp1", claim_ids=("rent_on_time",), audience="landlord"
        )
    if action is ActionType.SCHEDULE:
        base.update(
            payee_id=tainted("payee1"),
            amount=Tainted[Money](value=Money(amount_minor=100, currency=AUD), provenance=TYPED),
            schedule=ScheduleSpec(
                rrule=tainted("FREQ=MONTHLY", TYPED), first_run=tainted(date(2026, 11, 1), TYPED)
            ),
        )
    base.update(over)
    return ActionProposal(**base)


def risk(score_bps: int) -> RiskAssessment:
    level = (
        RiskLevel.CRITICAL
        if score_bps >= 9000
        else RiskLevel.HIGH
        if score_bps >= 6000
        else RiskLevel.LOW
    )
    return RiskAssessment(score_bps=score_bps, level=level, model_id="test")
