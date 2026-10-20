"""The real executor (docs/spec/modules.md 5.9): one transaction, fresh policy, grants, idempotency, ledger."""

from datetime import timedelta

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from corridor.confirm import lock_user
from corridor.contracts import ActionType, CurrencyCode
from corridor.contracts.confirm import ConfirmationLevel as L
from corridor.contracts.ledger import LedgerEventType
from corridor.executor import ExecutionRejected, Executor
from corridor.ledger import Ledger, LedgerError, verify_chain
from executor_helpers import (
    CFG,
    NOW,
    balance,
    count,
    grant_for,
    pay_landlord,
    run,
)  # fmt: skip

EX = None


@pytest.fixture
def ex(sandbox: Engine) -> Executor:
    return Executor(sandbox, CFG)


def chain_ok(engine: Engine) -> bool:
    with Session(engine) as s:
        return verify_chain(s).valid


def events(engine: Engine) -> list[str]:
    with engine.connect() as c:
        return [r[0] for r in c.execute(text("SELECT event_type FROM ledger_entries ORDER BY seq"))]


def test_a_confirmed_small_payment_executes_and_is_ledgered(sandbox: Engine, ex: Executor) -> None:
    p = pay_landlord(5_000)
    start = balance(sandbox, "acc_ramesh_qar")
    r = run(ex, p, grant_for(sandbox, p))
    assert r.amount_minor == 5_000 and r.balance_after_minor == start - 5_000 and not r.replayed
    assert balance(sandbox, "acc_ramesh_qar") == start - 5_000
    assert events(sandbox) == ["execution"] and chain_ok(sandbox)
    with sandbox.connect() as c:
        row = c.execute(
            text("SELECT proposal_hash, status FROM transfers WHERE id = :i"), {"i": r.transfer_id}
        ).one()
        used = c.execute(text("SELECT used FROM confirmation_grants")).scalar_one()
    assert row.proposal_hash == p.canonical_hash() and row.status == "executed" and used


def test_a_grant_below_the_fresh_decision_level_is_refused(sandbox: Engine, ex: Executor) -> None:
    p = pay_landlord(80_000)  # QAR 800 is above the low limit: MFA
    start = balance(sandbox, "acc_ramesh_qar")
    with pytest.raises(ExecutionRejected) as e:
        run(ex, p, grant_for(sandbox, p, L.CONFIRM))
    assert e.value.reason == "grant_level_too_low"
    assert (
        balance(sandbox, "acc_ramesh_qar") == start
        and count(sandbox, "transfers", "idempotency_key not like 'seed-%'") == 0
    )
    assert "execution_failed" in events(sandbox) and chain_ok(sandbox)
    assert count(sandbox, "security_events", "kind = 'failed_high_risk'") == 1
    assert (
        count(sandbox, "confirmation_grants", "used") == 0
    )  # the failed run did not burn the grant
    run(ex, p, grant_for(sandbox, p, L.MFA))
    assert balance(sandbox, "acc_ramesh_qar") == start - 80_000


def test_swap_attack_confirm_a_execute_b(sandbox: Engine, ex: Executor) -> None:
    a, b = pay_landlord(5_000), pay_landlord(6_000)
    start = balance(sandbox, "acc_ramesh_qar")
    with pytest.raises(ExecutionRejected) as e:
        run(ex, b, grant_for(sandbox, a))
    assert e.value.reason == "grant_hash_mismatch" and balance(sandbox, "acc_ramesh_qar") == start


def test_swapped_payee_changes_the_hash(sandbox: Engine, ex: Executor) -> None:
    a = pay_landlord(5_000, key="same-key")
    grant = grant_for(sandbox, a)
    other_payee = pay_landlord(5_000, key="same-key", payee="pay_ramesh_ama")
    with pytest.raises(ExecutionRejected):
        run(ex, other_payee, grant)


def test_replay_with_identical_terms_returns_the_earlier_result_without_paying_twice(
    sandbox: Engine, ex: Executor
) -> None:
    p = pay_landlord(5_000, key="idem-fixed")
    g = grant_for(sandbox, p)
    first = run(ex, p, g)
    start = balance(sandbox, "acc_ramesh_qar")
    again = run(ex, p, g)  # the grant is spent: the replay needs none
    assert again.replayed and again.transfer_id == first.transfer_id
    assert balance(sandbox, "acc_ramesh_qar") == start
    assert count(sandbox, "transfers", "id = :i".replace(":i", f"'{first.transfer_id}'")) == 1
    assert events(sandbox) == ["execution", "rejection"]


def test_a_reused_key_with_different_terms_is_rejected(sandbox: Engine, ex: Executor) -> None:
    p = pay_landlord(5_000, key="idem-fixed")
    run(ex, p, grant_for(sandbox, p))
    changed = pay_landlord(6_000, key="idem-fixed")
    with pytest.raises(ExecutionRejected) as e:
        run(ex, changed, grant_for(sandbox, changed))
    assert e.value.reason == "idempotency_key_reuse"
    other_user = pay_landlord(5_000, key="idem-fixed", user="usr_priya")
    with pytest.raises(ExecutionRejected) as e:
        run(ex, other_user, grant_for(sandbox, other_user))
    assert e.value.reason == "idempotency_key_reuse"


def test_expired_and_foreign_grants(sandbox: Engine, ex: Executor) -> None:
    p = pay_landlord(5_000)
    old = grant_for(sandbox, p, now=NOW - timedelta(seconds=121))
    with pytest.raises(ExecutionRejected) as e:
        run(ex, p, old)
    assert e.value.reason == "grant_expired"
    foreign = grant_for(sandbox, p, user="usr_priya")
    with pytest.raises(ExecutionRejected) as e:
        run(ex, p, foreign)
    assert e.value.reason == "grant_wrong_user"
    with pytest.raises(ExecutionRejected) as e:
        run(ex, p, grant_for(sandbox, p), user="usr_priya")  # not this session's user
    assert e.value.reason == "wrong_user"
    with pytest.raises(ExecutionRejected) as e:
        ex.execute(p, "no-such-grant", user_id="usr_ramesh", now=NOW, trace_id="t")
    assert e.value.reason == "grant_not_found"


def test_three_bad_grants_lock_the_user(sandbox: Engine, ex: Executor) -> None:
    p = pay_landlord(5_000)
    for _ in range(3):
        with pytest.raises(ExecutionRejected):
            run(ex, p, grant_for(sandbox, p, user="usr_priya"))
    with Session(sandbox) as s:
        assert s.execute(
            text("SELECT locked FROM safety_locks WHERE user_id='usr_ramesh'")
        ).scalar_one()
    assert "safety_lock" in events(sandbox)
    with pytest.raises(ExecutionRejected) as e:
        run(ex, p, grant_for(sandbox, p))
    assert e.value.reason == "policy_denied" and "R-LOCK" in e.value.rule_ids


def test_policy_is_rerun_at_execution_time(sandbox: Engine, ex: Executor) -> None:
    p = pay_landlord(5_000)
    g = grant_for(sandbox, p)
    with Session(sandbox) as s, s.begin():
        lock_user(s, "usr_ramesh", "manual", NOW)  # locked after the user confirmed
    with pytest.raises(ExecutionRejected) as e:
        run(ex, p, g)
    assert e.value.reason == "policy_denied" and e.value.rule_ids[0] == "R-LOCK"


def test_insufficient_funds_is_denied_by_policy(sandbox: Engine, ex: Executor) -> None:
    p = pay_landlord(10**8)
    start = balance(sandbox, "acc_ramesh_qar")
    with pytest.raises(ExecutionRejected) as e:
        run(ex, p, grant_for(sandbox, p, L.MFA))
    assert e.value.reason == "policy_denied" and "R-BALANCE" in e.value.rule_ids
    assert balance(sandbox, "acc_ramesh_qar") == start


def test_provenance_is_read_from_stored_state_not_the_proposal(
    sandbox: Engine, ex: Executor
) -> None:
    with sandbox.begin() as c:
        c.execute(
            text("UPDATE payees SET provenance = 'untrusted' WHERE id = 'pay_ramesh_landlord'")
        )
    p = pay_landlord(5_000)  # the proposal still claims a SYSTEM payee
    start = balance(sandbox, "acc_ramesh_qar")
    with pytest.raises(ExecutionRejected) as e:
        run(ex, p, grant_for(sandbox, p, L.CONFIRM))
    assert e.value.reason == "grant_level_too_low"  # the stored taint needs MFA
    r = run(ex, p, grant_for(sandbox, p, L.MFA))
    assert r.amount_minor == 5_000 and balance(sandbox, "acc_ramesh_qar") == start - 5_000


def test_other_users_accounts_and_payees_are_refused(sandbox: Engine, ex: Executor) -> None:
    bad_acct = pay_landlord(5_000, account="acc_priya_aud")
    with pytest.raises(ExecutionRejected) as e:
        run(ex, bad_acct, grant_for(sandbox, bad_acct))
    assert e.value.reason == "account_not_allowed"
    bad_payee = pay_landlord(5_000, payee="pay_priya_landlord")
    with pytest.raises(ExecutionRejected) as e:
        run(ex, bad_payee, grant_for(sandbox, bad_payee))
    assert e.value.reason == "policy_denied" and "R-OWNERSHIP" in e.value.rule_ids


def test_account_currency_must_match(sandbox: Engine, ex: Executor) -> None:
    p = pay_landlord(5_000, currency=CurrencyCode.NPR)
    with pytest.raises(ExecutionRejected) as e:
        run(ex, p, grant_for(sandbox, p))
    assert e.value.reason == "account_not_allowed"


def test_unsupported_and_incomplete_actions(sandbox: Engine, ex: Executor) -> None:
    conv = pay_landlord(
        5_000, action=ActionType.CONVERT, payee_id=None, target_currency=CurrencyCode.NPR
    )
    with pytest.raises(ExecutionRejected) as e:
        run(ex, conv, grant_for(sandbox, conv))
    assert e.value.reason == "unsupported_action"
    no_payee = pay_landlord(5_000).model_copy(update={"payee_id": None})
    with pytest.raises(ExecutionRejected) as e:
        run(ex, no_payee, grant_for(sandbox, no_payee))
    assert e.value.reason == "incomplete_proposal"


def test_invoice_payment_goes_only_to_the_stored_beneficiary(sandbox: Engine, ex: Executor) -> None:
    with sandbox.begin() as c:
        c.execute(text("UPDATE accounts SET balance_minor = 2000000 WHERE id = 'acc_priya_aud'"))
    kw = dict(user="usr_priya", account="acc_priya_aud", currency=CurrencyCode.AUD,
              action=ActionType.PAY_INVOICE, invoice_id="inv_priya_tuition_2026s2")  # fmt: skip
    wrong = pay_landlord(650_000, payee="pay_priya_landlord", **kw)
    with pytest.raises(ExecutionRejected) as e:
        run(ex, wrong, grant_for(sandbox, wrong, L.MFA))
    assert e.value.reason == "policy_denied" and "R-INVOICE-BENEFICIARY" in e.value.rule_ids
    right = pay_landlord(650_000, payee="pay_priya_university", **kw)
    r = run(ex, right, grant_for(sandbox, right, L.MFA))
    assert r.amount_minor == 650_000
    assert count(sandbox, "invoices", "id = 'inv_priya_tuition_2026s2' AND status = 'paid'") == 1
    twice = pay_landlord(650_000, payee="pay_priya_university", **kw)
    with pytest.raises(ExecutionRejected):
        run(ex, twice, grant_for(sandbox, twice, L.MFA))  # the invoice is no longer open


class FlakyLedger(Ledger):
    """A ledger that fails to write execution entries."""

    def append(self, session, **kw):  # type: ignore[no-untyped-def]
        if kw["event_type"] is LedgerEventType.EXECUTION:
            raise LedgerError("injected")
        return super().append(session, **kw)


def test_a_ledger_failure_fails_the_action_closed(sandbox: Engine) -> None:
    ex = Executor(sandbox, CFG, FlakyLedger())
    p = pay_landlord(5_000)
    start = balance(sandbox, "acc_ramesh_qar")
    g = grant_for(sandbox, p)
    with pytest.raises(ExecutionRejected) as e:
        run(ex, p, g)
    assert e.value.reason == "execution_error"
    assert balance(sandbox, "acc_ramesh_qar") == start  # nothing moved without a ledger entry
    assert count(sandbox, "transfers", "idempotency_key not like 'seed-%'") == 0
    assert count(sandbox, "confirmation_grants", "used") == 0
    assert chain_ok(sandbox)
    run(Executor(sandbox, CFG), p, g)  # a healthy ledger lets the same grant work
    assert balance(sandbox, "acc_ramesh_qar") == start - 5_000


def test_a_reused_key_returns_the_earlier_result_only_with_the_spent_grant(
    sandbox: Engine, ex: Executor
) -> None:
    p = pay_landlord(5_000, key="idem-fixed")
    first = run(ex, p, grant_for(sandbox, p))
    stranger = grant_for(sandbox, p)  # a fresh, unspent grant: not the one used for the payment
    with pytest.raises(ExecutionRejected) as e:
        run(ex, p, stranger)
    assert e.value.reason == "idempotency_key_reuse"
    with pytest.raises(ExecutionRejected) as e:
        ex.execute(p, "made-up", user_id="usr_ramesh", now=NOW, trace_id="t")
    assert e.value.reason == "idempotency_key_reuse"
    assert count(sandbox, "transfers", f"id = '{first.transfer_id}'") == 1


def test_a_successful_execution_stores_merkle_roots_so_deleting_it_is_detected(
    sandbox: Engine, ex: Executor
) -> None:
    p = pay_landlord(5_000)
    run(ex, p, grant_for(sandbox, p))
    assert count(sandbox, "ledger_roots") >= 2  # a day root and a session root
    with sandbox.begin() as c:
        for t in ("ledger_entries", "ledger_roots"):
            c.execute(text(f"ALTER TABLE {t} DISABLE TRIGGER {t}_append_only"))
        c.execute(text("DELETE FROM ledger_entries WHERE event_type = 'execution'"))
    with Session(sandbox) as s:
        assert not verify_chain(s).valid


def test_an_attack_rule_denial_at_execution_counts_toward_the_lock(
    sandbox: Engine, ex: Executor
) -> None:
    bad = pay_landlord(5_000, payee="pay_priya_landlord")  # someone else's payee: R-OWNERSHIP
    for _ in range(3):
        with pytest.raises(ExecutionRejected):
            run(ex, bad, grant_for(sandbox, bad))
    assert count(sandbox, "safety_locks", "locked") == 1


def test_truncate_is_refused_for_everyone(sandbox: Engine) -> None:
    from sqlalchemy.exc import DBAPIError

    for role in (None, "corridor_ledger"):
        with Session(sandbox) as s, pytest.raises(DBAPIError), s.begin():
            if role:
                s.execute(text(f"SET LOCAL ROLE {role}"))
            s.execute(text("TRUNCATE ledger_entries"))
