"""ADD_PAYEE through the real executor (P2-6): the payee comes from the stored document, is untrusted and
held, and nothing the proposal or a confirmation says can change that."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from corridor.contracts import ActionProposal, ActionType, CurrencyCode, Provenance, Tainted
from corridor.contracts.action import NewPayeeSpec
from corridor.contracts.confirm import ConfirmationLevel as L
from corridor.executor import ExecutionRejected, Executor, PayeeAdded
from corridor.sandbox import vault
from corridor.sandbox.doc_payee import payee_terms
from corridor.sandbox.models import DocumentRow, Payee
from executor_helpers import CFG, NOW, grant_for, pay_landlord, run

DOC = "d" * 64
FIELDS = {
    "kind": "invoice", "issuer": "CITYNEST", "beneficiary_name": "CityNest Bookings",
    "beneficiary_hash": vault.account_hash("927100334455"), "beneficiary_last4": "4455",
    "currency": "QAR", "matched_invoice_id": None,
}  # fmt: skip


@pytest.fixture
def ex(sandbox: Engine) -> Executor:
    with Session(sandbox) as s, s.begin():
        s.add(
            DocumentRow(
                doc_hash=DOC,
                user_id="usr_ramesh",
                kind="invoice",
                injection_suspected=False,
                signals=[],
                fields=FIELDS,
                provenance="untrusted",
                created_at=NOW,
            )
        )
    return Executor(sandbox, CFG)


def add_proposal(**over: object) -> ActionProposal:
    t = payee_terms(FIELDS)
    assert t is not None

    def u(v: str) -> Tainted[str]:
        return Tainted[str](value=v, provenance=Provenance.UNTRUSTED, source_ref=DOC[:16])

    spec = NewPayeeSpec(
        display_name=u(over.pop("name", t.name)),  # type: ignore[arg-type]
        region=u(t.region), currency=CurrencyCode(t.currency),
        account_no=u(over.pop("ref", t.account_ref)),  # type: ignore[arg-type]
    )  # fmt: skip
    return ActionProposal(
        proposal_id=f"prop_{uuid.uuid4().hex[:8]}", action=ActionType.ADD_PAYEE, user_id="usr_ramesh",
        source_account_id=Tainted[str](value="acc_ramesh_qar", provenance=Provenance.SYSTEM),
        new_payee=spec, derived_from_docs=(DOC,), idempotency_key=f"idem-{uuid.uuid4().hex}",
    )  # fmt: skip


def test_saves_an_untrusted_unverified_payee_held_for_24_hours(
    sandbox: Engine, ex: Executor
) -> None:
    p = add_proposal()
    out = run(ex, p, grant_for(sandbox, p, L.MFA))
    assert isinstance(out, PayeeAdded) and out.hold_until - NOW == timedelta(
        hours=CFG.payee_cooloff.hours
    )
    with Session(sandbox) as s:
        row = s.get(Payee, out.payee_id)
        assert row is not None and row.provenance == "untrusted" and row.verified_at is None
        assert row.added_via == "document" and row.account_no_masked == "****4455"


def test_a_confirmed_document_payee_still_cannot_be_paid_within_24_hours(
    sandbox: Engine, ex: Executor
) -> None:
    p = add_proposal()
    out = run(ex, p, grant_for(sandbox, p, L.MFA))
    assert isinstance(out, PayeeAdded)
    pay = pay_landlord(100, payee=out.payee_id)  # QAR 1.00, with the strongest grant there is
    with pytest.raises(ExecutionRejected) as e:
        run(ex, pay, grant_for(sandbox, pay, L.MFA))
    assert e.value.reason == "policy_denied" and "R-PAYEE-COOLOFF" in e.value.rule_ids
    # only time ends it: 25 hours later the same payment is allowed (by an MFA grant)
    later = NOW + timedelta(hours=25)
    pay2 = pay_landlord(100, payee=out.payee_id)
    assert run(ex, pay2, grant_for(sandbox, pay2, L.MFA, now=later), now=later) is not None


def test_a_proposal_that_disagrees_with_the_stored_document_is_refused(
    sandbox: Engine, ex: Executor
) -> None:
    for bad in ({"name": "Someone Else"}, {"ref": "doc_acct_" + "0" * 16}):
        p = add_proposal(**bad)
        with pytest.raises(ExecutionRejected) as e:
            run(ex, p, grant_for(sandbox, p, L.MFA))
        assert e.value.reason == "document_mismatch"


def test_the_same_account_cannot_be_saved_twice_and_a_grant_is_required(
    sandbox: Engine, ex: Executor
) -> None:
    p = add_proposal()
    with pytest.raises(
        ExecutionRejected
    ) as e:  # a confirm-level grant is below the MFA the rule needs
        run(ex, p, grant_for(sandbox, p, L.CONFIRM))
    assert e.value.reason.startswith("grant_")
    p1 = add_proposal()
    run(ex, p1, grant_for(sandbox, p1, L.MFA))
    p2 = add_proposal()
    with pytest.raises(ExecutionRejected) as e:
        run(ex, p2, grant_for(sandbox, p2, L.MFA))
    assert e.value.reason == "payee_exists"
    with Session(sandbox) as s:
        n = len(s.scalars(select(Payee).where(Payee.added_via == "document")).all())
    assert n == 1
