"""Instalment payments and contribution requests in the real executor (P3-1, ADR 0014)."""

import uuid

import pytest
from sqlalchemy import Engine, text

from corridor.contracts import ActionType, CurrencyCode, Money, Provenance, Tainted
from corridor.contracts.action import ActionProposal, ContributionSpec
from corridor.contracts.confirm import ConfirmationLevel as L
from corridor.executor import ContributionRequested, ExecutionRejected, Executor
from executor_helpers import CFG, balance, count, grant_for, pay_landlord, run

INV = "inv_priya_tuition_2026s2"  # AUD 6,500.00 owed to pay_priya_university


@pytest.fixture
def ex(sandbox: Engine) -> Executor:
    return Executor(sandbox, CFG)


def instalment(amount: int, account: str = "acc_priya_aud") -> ActionProposal:
    return pay_landlord(
        amount, user="usr_priya", account=account, payee="pay_priya_university",
        currency=CurrencyCode.AUD, action=ActionType.PAY_INVOICE, invoice_id=INV,
    )  # fmt: skip


def status_and_paid(engine: Engine) -> tuple[str, int]:
    with engine.connect() as c:
        r = c.execute(text(f"SELECT status, paid_minor FROM invoices WHERE id = '{INV}'")).one()
    return str(r[0]), int(r[1])


def test_instalments_add_up_and_only_the_last_one_marks_the_invoice_paid(
    sandbox: Engine, ex: Executor
) -> None:
    with sandbox.begin() as c:
        c.execute(text("UPDATE accounts SET balance_minor = 900000 WHERE id = 'acc_priya_aud'"))
    for amount, expected in ((370_000, ("part_paid", 370_000)), (150_000, ("part_paid", 520_000))):
        p = instalment(amount, "acc_priya_aud_savings" if amount == 150_000 else "acc_priya_aud")
        run(ex, p, grant_for(sandbox, p, L.MFA))
        assert status_and_paid(sandbox) == expected
    last = instalment(130_000)
    run(ex, last, grant_for(sandbox, last, L.MFA))
    assert status_and_paid(sandbox) == ("paid", 650_000)
    assert balance(sandbox, "acc_priya_aud_savings") == 0


def test_an_instalment_can_never_overpay_what_is_still_owed(sandbox: Engine, ex: Executor) -> None:
    with sandbox.begin() as c:
        c.execute(text("UPDATE accounts SET balance_minor = 2000000 WHERE id = 'acc_priya_aud'"))
    first = instalment(600_000)
    run(ex, first, grant_for(sandbox, first, L.MFA))
    over = instalment(60_000)  # only 50,000 is still owed
    with pytest.raises(ExecutionRejected) as e:
        run(ex, over, grant_for(sandbox, over, L.MFA))
    assert e.value.reason == "policy_denied" and "R-INVOICE-BENEFICIARY" in e.value.rule_ids
    assert status_and_paid(sandbox) == ("part_paid", 600_000)


def request(
    amount: int = 100_000, who: str = "usr_ramesh", currency: CurrencyCode = CurrencyCode.QAR
) -> ActionProposal:
    return ActionProposal(
        proposal_id=f"prop_{uuid.uuid4().hex[:8]}", action=ActionType.REQUEST_CONTRIBUTION,
        user_id="usr_priya",
        source_account_id=Tainted[str](value="acc_priya_aud", provenance=Provenance.SYSTEM),
        amount=Tainted[Money](value=Money(amount_minor=amount, currency=currency), provenance=Provenance.SYSTEM),
        invoice_id=INV, idempotency_key=f"idem-{uuid.uuid4().hex}",
        contribution=ContributionSpec(contributor_user_id=who, target_account_id="acc_priya_aud"),
    )  # fmt: skip


def test_a_contribution_request_is_stored_ledgered_and_moves_no_money(
    sandbox: Engine, ex: Executor
) -> None:
    before = (balance(sandbox, "acc_priya_aud"), balance(sandbox, "acc_ramesh_qar"))
    p = request()
    r = run(ex, p, grant_for(sandbox, p, L.CONFIRM))
    assert isinstance(r, ContributionRequested) and r.contributor_user_id == "usr_ramesh"
    assert (
        count(sandbox, "contribution_requests", "status = 'pending' AND amount_minor = 100000") == 1
    )
    assert (balance(sandbox, "acc_priya_aud"), balance(sandbox, "acc_ramesh_qar")) == before
    assert count(sandbox, "ledger_entries", "event_type = 'execution'") == 1


def test_a_request_to_someone_who_is_not_a_linked_family_member_is_denied(
    sandbox: Engine, ex: Executor
) -> None:
    p = request(who="usr_anil")
    with pytest.raises(ExecutionRejected) as e:
        run(ex, p, grant_for(sandbox, p, L.CONFIRM))
    assert "R-CONTRIBUTION" in e.value.rule_ids
    assert count(sandbox, "contribution_requests") == 0


def test_a_request_over_the_link_limit_is_denied_and_a_replayed_key_does_nothing(
    sandbox: Engine, ex: Executor
) -> None:
    big = request(amount=500_001)
    with pytest.raises(ExecutionRejected):
        run(ex, big, grant_for(sandbox, big, L.CONFIRM))
    p = request()
    run(ex, p, grant_for(sandbox, p, L.CONFIRM))
    with pytest.raises(ExecutionRejected):
        run(ex, p, grant_for(sandbox, p, L.CONFIRM))  # same idempotency key: no second request
    assert count(sandbox, "contribution_requests") == 1
