"""A new landlord Priya typed herself: the AUD 2,000 deposit is neither critical nor a lock (P3-1b).

Real policy, real risk score and the real lock counter over the seeded sandbox. A payee the user typed
(``user_typed``) is not tainted: it can raise the score only through "new payee" (and the amount and
hour), which cannot reach the critical threshold on its own. What does refuse a payment on the day the
payee was saved is the existing cooling off rule (R-PAYEE-COOLOFF, first payment cap), not the risk model."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from corridor.confirm import record_policy_denial
from corridor.contracts import CurrencyCode, Provenance
from corridor.contracts.policy import Decision
from corridor.contracts.risk import RiskLevel
from corridor.policy import evaluate
from corridor.risk import assess
from corridor.sandbox import vault
from corridor.sandbox.account_state import build_account_state
from corridor.sandbox.models import Payee
from executor_helpers import CFG, pay_landlord

NOON = datetime(2026, 10, 12, 2, 0, tzinfo=UTC)  # 13:00 in Priya's time zone
SMALL_HOURS = datetime(
    2026, 10, 11, 16, 0, tzinfo=UTC
)  # 03:00 in Sydney: the worst hour for the score
DEPOSIT = 200_000  # AUD 2,000.00


def add_typed_payee(engine: Engine, saved_at: datetime) -> None:
    with Session(engine) as s, s.begin():
        s.add(
            Payee(
                id="pay_priya_new_landlord", user_id="usr_priya", display_name="Okafor Property",
                region="AU", currency="AUD", provider_ref=None, relation=None,
                account_no_masked="****1234", account_no_hash=vault.account_hash("062000771234"),
                verified_at=None, added_via="agent_mfa", created_at=saved_at,
                provenance=Provenance.USER_TYPED.value,
            )
        )  # fmt: skip


def decide(engine: Engine, now: datetime):  # type: ignore[no-untyped-def]
    p = pay_landlord(
        DEPOSIT, user="usr_priya", payee="pay_priya_new_landlord", account="acc_priya_aud",
        currency=CurrencyCode.AUD,
    )  # fmt: skip
    with Session(engine) as s:
        state = build_account_state(s, "usr_priya", now, CFG)
        return evaluate(p, state, CFG, now), assess(p, state, CFG, now)


def test_two_days_after_typing_the_account_the_deposit_needs_mfa_and_is_not_critical(
    sandbox: Engine,
) -> None:
    add_typed_payee(sandbox, NOON - timedelta(days=2))
    decision, risk = decide(sandbox, NOON)
    assert decision.decision is Decision.REQUIRE_MFA  # a payee under 30 days old always needs MFA
    assert risk.level is not RiskLevel.CRITICAL
    assert {r.feature for r in risk.top_reasons} <= {
        "new_payee",
        "amount_unusual",
        "amount_extreme",
    }
    assert "R-RISK" not in decision.rule_ids  # the risk rule did not add anything


def test_even_in_the_small_hours_it_stays_below_critical(sandbox: Engine) -> None:
    add_typed_payee(sandbox, SMALL_HOURS - timedelta(days=2))
    decision, risk = decide(sandbox, SMALL_HOURS)
    assert risk.level is not RiskLevel.CRITICAL and risk.score_bps < CFG.risk.critical_bps
    assert decision.decision is Decision.REQUIRE_MFA


def test_on_the_day_it_was_saved_the_cooloff_rule_refuses_it_but_nothing_locks(
    sandbox: Engine,
) -> None:
    """The payee was typed an hour ago. This is the existing first payment cap, not the risk model."""
    add_typed_payee(sandbox, NOON - timedelta(hours=1))
    decision, risk = decide(sandbox, NOON)
    assert decision.decision is Decision.DENY and "R-PAYEE-COOLOFF" in decision.rule_ids
    assert "R-RISK.risk_critical" not in decision.reasons
    assert risk.level is not RiskLevel.CRITICAL
    with Session(sandbox) as s, s.begin():
        locked = record_policy_denial(s, "usr_priya", decision, NOON, CFG, ())
    assert locked is None  # one refusal is not three, and it was not a critical score


@pytest.mark.parametrize("attempts", [1, 2])
def test_one_or_two_refusals_do_not_lock_and_do_not_make_the_next_one_critical(
    sandbox: Engine, attempts: int
) -> None:
    add_typed_payee(sandbox, NOON - timedelta(hours=1))
    for i in range(attempts):
        decision, _ = decide(sandbox, NOON + timedelta(minutes=i))
        with Session(sandbox) as s, s.begin():
            assert (
                record_policy_denial(s, "usr_priya", decision, NOON + timedelta(minutes=i), CFG, ())
                is None
            )
    _, risk = decide(sandbox, NOON + timedelta(minutes=5))
    assert risk.level is not RiskLevel.CRITICAL


def test_after_the_bank_verifies_the_typed_payee_the_same_day_deposit_needs_mfa_and_is_allowed(
    sandbox: Engine,
) -> None:
    """ADR 0018: a bank officer's verification (Confirmation of Payee match) ends the cap for a payee the
    user typed. The deposit then needs MFA like any payment to a new payee. It is never critical."""
    add_typed_payee(sandbox, NOON - timedelta(hours=1))
    before, _ = decide(sandbox, NOON)
    assert before.decision is Decision.DENY and "R-PAYEE-COOLOFF" in before.rule_ids
    with Session(sandbox) as s, s.begin():
        row = s.get(Payee, "pay_priya_new_landlord")
        assert row is not None
        row.verified_at = NOON - timedelta(minutes=5)
    after, risk = decide(sandbox, NOON)
    assert after.decision is Decision.REQUIRE_MFA and "R-PAYEE-COOLOFF" not in after.rule_ids
    assert risk.level is not RiskLevel.CRITICAL


def test_verification_never_releases_a_payee_that_came_from_a_document(sandbox: Engine) -> None:
    add_typed_payee(sandbox, NOON - timedelta(hours=1))
    with Session(sandbox) as s, s.begin():
        row = s.get(Payee, "pay_priya_new_landlord")
        assert row is not None
        row.provenance, row.verified_at = Provenance.UNTRUSTED.value, NOON
    decision, _ = decide(sandbox, NOON)
    assert decision.decision is Decision.DENY and "R-PAYEE-COOLOFF" in decision.rule_ids
