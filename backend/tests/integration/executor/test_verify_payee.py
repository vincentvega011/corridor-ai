"""Independent payee verification (ADR 0012): only a bank officer, only after a CoP match."""

import uuid
from datetime import date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from corridor.agent.service import AgentService
from corridor.api.app import create_app
from corridor.api.auth import TokenAuth
from corridor.config import AppEnv, Settings
from corridor.contracts import ActionProposal, ActionType, CurrencyCode, Provenance, Tainted
from corridor.contracts.action import NewPayeeSpec
from corridor.contracts.confirm import ConfirmationLevel as L
from corridor.executor import (
    ExecutionRejected,
    Executor,
    PayeeAdded,
    VerificationRefused,
    verify_payee,
)
from corridor.sandbox import synth, vault
from corridor.sandbox.cop import names_match
from corridor.sandbox.doc_payee import payee_terms
from corridor.sandbox.models import DocumentRow, Payee
from executor_helpers import CFG, NOW, grant_for, pay_landlord, run

SCAM = ("attacker:housing-pdf", "2208", "CityNest Bookings")
GENUINE = ("attacker:housing-genuine", "6612", "Wattle Lane Property")
USER = "usr_priya"


def add_from_document(engine: Engine, who: tuple[str, str, str], doc: str) -> str:
    """Save a document payee for Priya through the real executor and return its id."""
    key, last4, name = who
    digits = synth.attacker_account_number(key, last4)
    sdir = engine.secrets_dir  # type: ignore[attr-defined]
    fields = {
        "kind": "invoice", "issuer": name.upper(), "beneficiary_name": name,
        "beneficiary_hash": vault.account_hash(digits, sdir), "beneficiary_last4": last4,
        "currency": "AUD", "matched_invoice_id": None,
    }  # fmt: skip
    with Session(engine) as s, s.begin():
        s.add(
            DocumentRow(
                doc_hash=doc * 64,
                user_id=USER,
                kind="invoice",
                injection_suspected=False,
                signals=[],
                fields=fields,
                provenance="untrusted",
                created_at=NOW,
            )
        )
    t = payee_terms(fields)
    assert t is not None

    def u(v: str) -> Tainted[str]:
        return Tainted[str](value=v, provenance=Provenance.UNTRUSTED, source_ref=doc)

    p = ActionProposal(
        proposal_id=f"prop_{uuid.uuid4().hex[:8]}", action=ActionType.ADD_PAYEE, user_id=USER,
        source_account_id=Tainted[str](value="acc_priya_aud", provenance=Provenance.SYSTEM),
        new_payee=NewPayeeSpec(
            display_name=u(t.name), region=u(t.region), currency=CurrencyCode.AUD,
            account_no=u(t.account_ref),
        ),
        derived_from_docs=(doc * 64,), idempotency_key=f"idem-{uuid.uuid4().hex}",
    )  # fmt: skip
    out = run(Executor(engine, CFG), p, grant_for(engine, p, L.MFA))
    assert isinstance(out, PayeeAdded)
    return out.payee_id


def officer_verify(
    engine: Engine,
    payee_id: str,
    reason: str = "CoP checked",
    now=NOW,
    method: str = "callback_independent_number",
):  # type: ignore[no-untyped-def]
    return verify_payee(
        engine, CFG, officer_id="officer_demo_1", payee_id=payee_id, method=method, reference=reason, now=now,
        trace_id="trace-verify",
    )  # fmt: skip


def payee_verified(engine: Engine, payee_id: str) -> bool:
    with Session(engine) as s:
        row = s.get(Payee, payee_id)
        return row is not None and row.verified_at is not None


def entries(engine: Engine, kind: str) -> list[str]:
    with engine.connect() as c:
        return [
            r[0]
            for r in c.execute(
                text(
                    "SELECT payload_canonical_json FROM ledger_entries WHERE event_type = :k ORDER BY seq"
                ),
                {"k": kind},
            )
        ]


def test_a_cop_mismatch_can_never_be_verified_and_is_ledgered(sandbox: Engine) -> None:
    pid = add_from_document(sandbox, SCAM, "a")
    with pytest.raises(VerificationRefused) as e:
        officer_verify(sandbox, pid)
    assert e.value.reason == "cop_mismatch"
    with Session(sandbox) as s:
        assert s.get(Payee, pid).verified_at is None  # type: ignore[union-attr]
    log = "".join(entries(sandbox, "rejection"))
    assert "officer_demo_1" in log and "cop_mismatch" in log and "verify_payee" in log


def test_a_genuine_landlord_is_verified_after_a_cop_match_and_the_officer_is_ledgered(
    sandbox: Engine,
) -> None:
    pid = add_from_document(sandbox, GENUINE, "b")
    done = officer_verify(sandbox, pid, "Lease and CoP checked by phone")
    assert done.officer_id == "officer_demo_1"
    with Session(sandbox) as s:
        row = s.get(Payee, pid)
        assert row is not None and row.verified_at is not None and row.provenance == "untrusted"
    log = "".join(entries(sandbox, "execution"))
    assert '"action":"verify_payee"' in log.replace(" ", "") and "officer_demo_1" in log
    assert "Lease and CoP checked by phone" in log


def test_no_record_no_reason_and_double_verification_are_refused(sandbox: Engine) -> None:
    pid = add_from_document(sandbox, GENUINE, "c")
    with pytest.raises(VerificationRefused) as e:
        officer_verify(sandbox, pid, reason="   ")
    assert e.value.reason == "reference_required"
    officer_verify(sandbox, pid)
    with pytest.raises(VerificationRefused) as e:
        officer_verify(sandbox, pid)
    assert e.value.reason == "already_verified"
    with pytest.raises(VerificationRefused) as e:
        officer_verify(sandbox, "pay_does_not_exist")
    assert e.value.reason == "payee_not_found"
    unknown = add_from_document(sandbox, ("attacker:housing-inj", "4420", "Unknown Holder"), "d")
    with pytest.raises(VerificationRefused) as e:
        officer_verify(sandbox, unknown)
    assert e.value.reason == "cop_no_record"


def test_after_the_hold_a_verified_payee_is_paid_with_mfa_and_an_unverified_one_is_capped(
    sandbox: Engine,
) -> None:
    ex = Executor(sandbox, CFG)
    genuine = add_from_document(sandbox, GENUINE, "e")
    later = NOW + timedelta(hours=25)

    def pay(pid: str) -> object:
        p = pay_landlord(
            200_000, user=USER, payee=pid, account="acc_priya_aud", currency=CurrencyCode.AUD
        )
        return run(ex, p, grant_for(sandbox, p, L.MFA, now=later), now=later)

    with pytest.raises(
        ExecutionRejected
    ) as e:  # AUD 2,000 is over the NPR 5,000 cap while unverified
        pay(genuine)
    assert "R-PAYEE-COOLOFF" in e.value.rule_ids
    officer_verify(sandbox, genuine, now=later)
    assert (
        pay(genuine) is not None
    )  # verified: the cap is lifted, MFA still required (untrusted origin)
    p = pay_landlord(
        200_000, user=USER, payee=genuine, account="acc_priya_aud", currency=CurrencyCode.AUD
    )
    with pytest.raises(
        ExecutionRejected
    ):  # a confirm-only grant is not enough for an untrusted payee
        run(ex, p, grant_for(sandbox, p, L.CONFIRM, now=later), now=later)


def test_no_amount_of_input_can_verify_a_cop_mismatch() -> None:
    from hypothesis import given, settings
    from hypothesis import strategies as st

    @settings(max_examples=200)
    @given(st.text(max_size=40), st.text(max_size=40))
    def prop(payee_name: str, holder: str) -> None:
        # a match needs the same words: a name sharing no word with the holder never matches
        import re

        def words(x: str) -> set[str]:
            return set(re.findall(r"[a-z0-9]+", x.lower()))

        if not words(payee_name) & words(holder):
            assert not names_match(payee_name, holder)

    prop()
    assert not names_match("CityNest Bookings", "Marcus Tan Holdings")
    assert names_match("Wattle Lane Property", "Wattle Lane Property Pty Ltd")
    assert not names_match(
        "Wattle", "Wattle Lane Property Pty Ltd"
    )  # one shared word is not enough


async def test_a_customer_cannot_verify_a_payee_by_the_api_or_any_user_path(
    service: AgentService, sandbox: Engine
) -> None:
    pid = add_from_document(sandbox, GENUINE, "f")
    settings = Settings(app_env=AppEnv.DEV, frontend_origin="http://localhost:3000")
    user_token = "demo_user_token_for_priya"
    app = create_app(
        settings=settings, service=service, engine=sandbox, auth=TokenAuth({USER: user_token}),
        today=lambda: date(2026, 10, 10), officer_token="officer_secret_token",
    )  # fmt: skip
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 5000))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
        url = f"/officer/payees/{pid}/verify"
        body = {"method": "in_branch_check", "reference": "I am the customer, please verify"}
        for headers in (
            {},
            {"Authorization": f"Bearer {user_token}"},
            {"Authorization": "Bearer nope"},
        ):
            r = await c.post(url, json=body, headers=headers)
            assert r.status_code == 401
        with Session(sandbox) as s:
            assert s.get(Payee, pid).verified_at is None  # type: ignore[union-attr]
        ok = await c.post(url, json=body, headers={"Authorization": "Bearer officer_secret_token"})
        assert ok.status_code == 200 and ok.json()["officer_id"] == "officer_demo_1"
        again = await c.post(
            url, json=body, headers={"Authorization": "Bearer officer_secret_token"}
        )
        assert again.status_code == 409 and again.json()["detail"] == "already_verified"
    # the agent cannot propose or run it: there is no action type, and no chat path writes verified_at
    assert "verify_payee" not in {a.value for a in ActionType}


def test_get_payees_gives_a_fixed_label_and_a_flag_never_the_raw_document_name(
    sandbox: Engine,
) -> None:
    from corridor.agent.toolrun import run_read_tool

    pid = add_from_document(sandbox, SCAM, "9")
    res = run_read_tool(sandbox, USER, "get_payees", {}, date(2026, 10, 10))
    item = next(p for p in res.data["payees"] if p["payee_id"] == pid)
    assert item["display_name"] == "Payee from document ****2208"
    assert item["name_untrusted"] is True and item["provenance"] == "untrusted"
    assert "CityNest" not in str(res.model_dump(mode="json"))
    stored = next(p for p in res.data["payees"] if p["payee_id"] == "pay_priya_university")
    assert stored["name_untrusted"] is False and "Harbourview" in stored["display_name"]


def test_no_verification_without_a_method_and_a_reference_and_all_three_are_ledgered(
    sandbox: Engine,
) -> None:
    """ADR 0019: the CoP result, an out-of-band method from the fixed list and a free text reference."""
    pid = add_from_document(sandbox, GENUINE, "e")
    with pytest.raises(VerificationRefused) as e:
        officer_verify(sandbox, pid, method="phone_the_number_on_the_pdf")
    assert e.value.reason == "method_required"
    with pytest.raises(VerificationRefused) as e:
        officer_verify(sandbox, pid, reason="ok")
    assert e.value.reason == "reference_required"
    assert payee_verified(sandbox, pid) is False  # neither attempt verified it
    officer_verify(
        sandbox,
        pid,
        reason="called the housing office on 02 5550 0100",
        method="university_housing_office_list",
    )
    log = "".join(entries(sandbox, "execution")).replace(" ", "")
    assert '"method":"university_housing_office_list"' in log
    assert "calledthehousingoffice" in log and '"cop":"match"' in log and "officer_demo_1" in log
    assert payee_verified(sandbox, pid) is True


def test_every_method_on_the_list_works_and_the_cop_result_is_ledgered_on_a_refusal(
    sandbox: Engine,
) -> None:
    from corridor.executor.verify import VERIFICATION_METHODS

    assert set(VERIFICATION_METHODS) == {
        "callback_independent_number",
        "in_branch_check",
        "university_housing_office_list",
    }
    pid = add_from_document(sandbox, SCAM, "f")
    with pytest.raises(VerificationRefused) as e:
        officer_verify(sandbox, pid, reason="the agent said it is fine", method="in_branch_check")
    assert (
        e.value.reason == "cop_mismatch"
    )  # a mismatch can never be verified, whatever method is claimed
    assert '"cop":"mismatch"' in "".join(entries(sandbox, "rejection")).replace(" ", "")


def test_officer_verification_never_shortens_the_hold_of_a_document_payee(sandbox: Engine) -> None:
    """t32: verified the same day, a document derived payee is still held (cap 0); after 24 hours it pays with MFA."""
    ex = Executor(sandbox, CFG)
    genuine = add_from_document(sandbox, GENUINE, "f")
    same_day = NOW + timedelta(hours=2)
    officer_verify(sandbox, genuine, now=same_day)

    def pay(at: datetime) -> object:
        p = pay_landlord(
            200_000, user=USER, payee=genuine, account="acc_priya_aud", currency=CurrencyCode.AUD
        )
        return run(ex, p, grant_for(sandbox, p, L.MFA, now=at), now=at)

    with pytest.raises(ExecutionRejected) as e:
        pay(same_day)
    assert "R-PAYEE-COOLOFF" in e.value.rule_ids
    assert pay(NOW + timedelta(hours=25)) is not None


def test_split_payments_over_five_days_to_a_document_payee_move_at_most_the_cap_per_day(
    sandbox: Engine,
) -> None:
    """RT-121 at the executor level (no model): two AUD 40 payments a day for five days, each with MFA.

    After the 24 hour hold an unverified document account takes NPR 5,000 in total in any rolling 24 hours, so one
    payment a day passes and the second is denied: the bounded total is 5 payments of AUD 40 (about NPR 21,530)."""
    ex = Executor(sandbox, CFG)
    pid = add_from_document(sandbox, GENUINE, "9")
    paid = refused = 0
    for day in range(1, 6):
        at = NOW + timedelta(hours=25 * day)
        for _ in range(2):
            p = pay_landlord(
                4_000, user=USER, payee=pid, account="acc_priya_aud", currency=CurrencyCode.AUD
            )
            try:
                run(ex, p, grant_for(sandbox, p, L.MFA, now=at), now=at)
                paid += 1
            except ExecutionRejected as e:
                assert "R-PAYEE-COOLOFF" in e.rule_ids
                refused += 1
    assert (paid, refused) == (5, 5)


def test_split_payments_over_five_days_to_a_typed_payee_are_bounded_by_the_daily_limit(
    sandbox: Engine,
) -> None:
    """RT-121b at the executor level (no model): three AUD 4,000 payments a day for five days, each with MFA.

    A payee the user typed has no cap after its 24 hour hold, so only the daily limit (NPR 1,000,000 in any
    rolling 24 hours) bounds it: two payments a day pass, the third is denied. Bounded total: 10 payments of
    AUD 4,000 (about NPR 4.3 million), never more than 5 x NPR 1,000,000. A documented limitation."""
    from corridor.sandbox.models import Payee

    digits = synth.attacker_account_number("attacker:typed-five-day", "5151")
    with Session(sandbox) as s, s.begin():
        s.add(
            Payee(
                id="pay_typed_5d", user_id=USER, display_name="Rajan Karki", region="AU",
                currency="AUD", provider_ref=None, relation=None, account_no_masked="****5151",
                account_no_hash=vault.account_hash(digits, sandbox.secrets_dir),  # type: ignore[attr-defined]
                verified_at=None, added_via="agent_mfa", created_at=NOW, provenance="user_typed",
            )
        )  # fmt: skip
    with sandbox.begin() as c:
        c.execute(text("UPDATE accounts SET balance_minor = 50000000 WHERE id = 'acc_priya_aud'"))
    ex = Executor(sandbox, CFG)
    paid = refused = 0
    for day in range(1, 6):
        at = NOW + timedelta(hours=25 * day)
        for _ in range(3):
            p = pay_landlord(
                400_000, user=USER, payee="pay_typed_5d", account="acc_priya_aud",
                currency=CurrencyCode.AUD,
            )  # fmt: skip
            try:
                run(ex, p, grant_for(sandbox, p, L.MFA, now=at), now=at)
                paid += 1
            except ExecutionRejected:
                refused += 1
    assert (paid, refused) == (10, 5)
