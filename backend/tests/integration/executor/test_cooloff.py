"""R-PAYEE-COOLOFF through the real executor: a valid grant and a valid MFA level still cannot move
money above the cap to an account saved less than 24 hours ago (the fooled-user threat)."""

from datetime import timedelta

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from corridor.contracts.confirm import ConfirmationLevel as L
from corridor.executor import ExecutionRejected, Executor
from corridor.sandbox import vault
from corridor.sandbox.models import Payee
from executor_helpers import CFG, NOW, balance, count, grant_for, pay_landlord, run

ACC = "acc_ramesh_qar"


@pytest.fixture
def ex(sandbox: Engine) -> Executor:
    return Executor(sandbox, CFG)


def add_payee(
    engine: Engine, pid: str, digits: str, hours_ago: float, prov: str = "untrusted"
) -> None:
    with Session(engine) as s, s.begin():
        s.add(
            Payee(
                id=pid, user_id="usr_ramesh", display_name=pid, region="QA", currency="QAR",
                account_no_masked="****" + digits[-4:], account_no_hash=vault.account_hash(digits),
                verified_at=None, added_via="document", created_at=NOW - timedelta(hours=hours_ago),
                provenance=prov,
            )
        )  # fmt: skip


def attempt(ex: Executor, engine: Engine, pid: str, minor: int) -> str | None:
    p = pay_landlord(minor, payee=pid)
    try:
        run(ex, p, grant_for(engine, p, L.MFA))
    except ExecutionRejected as e:
        return f"{e.reason}:{','.join(e.rule_ids)}"
    return None


def test_a_valid_mfa_grant_cannot_pay_a_fresh_account_above_the_cap(
    sandbox: Engine, ex: Executor
) -> None:
    add_payee(sandbox, "pay_scam", "98765432101", 1)
    start = balance(sandbox, ACC)
    out = attempt(ex, sandbox, "pay_scam", 100_000)  # QAR 1,000 is far above NPR 5,000
    assert out is not None and out.startswith("policy_denied") and "R-PAYEE-COOLOFF" in out
    assert balance(sandbox, ACC) == start


def test_an_untrusted_origin_account_is_held_even_for_a_small_amount(
    sandbox: Engine, ex: Executor
) -> None:
    add_payee(sandbox, "pay_scam", "98765432101", 1)  # provenance untrusted (from a document)
    start = balance(sandbox, ACC)
    out = attempt(
        ex, sandbox, "pay_scam", 5_000
    )  # QAR 50 is under the typed cap, but not for this account
    assert out is not None and "R-PAYEE-COOLOFF" in out
    assert balance(sandbox, ACC) == start


def test_splitting_and_renaming_do_not_get_around_the_cap(sandbox: Engine, ex: Executor) -> None:
    add_payee(sandbox, "pay_scam", "98765432101", 1, "user_typed")  # an account the user typed
    add_payee(
        sandbox, "pay_scam_renamed", "98765432101", 0.2, "user_typed"
    )  # the same account under another name
    assert attempt(ex, sandbox, "pay_scam", 5_000) is None  # QAR 50: under the cap
    assert attempt(ex, sandbox, "pay_scam_renamed", 5_000) is None  # QAR 100 in total, still under
    out = attempt(ex, sandbox, "pay_scam_renamed", 100_000)
    assert out is not None and "R-PAYEE-COOLOFF" in out
    out = attempt(ex, sandbox, "pay_scam", 100_000)
    assert out is not None and "R-PAYEE-COOLOFF" in out
    sent = count(sandbox, "transfers", "idempotency_key not like 'seed-%' and status = 'executed'")
    assert sent == 2


def test_the_cap_ends_24_hours_after_the_account_was_first_saved(
    sandbox: Engine, ex: Executor
) -> None:
    add_payee(sandbox, "pay_old", "11122233344", 25)
    assert (
        attempt(ex, sandbox, "pay_old", 6_000) is None
    )  # NPR cap no longer applies (MFA grant used)
