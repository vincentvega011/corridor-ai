"""Grants, TOTP, safety lock and the USER_CONFIRMED upgrade (docs/spec/modules.md 5.8, INV-5, INV-11)."""

import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from corridor.confirm import (
    ConfirmationService,
    GrantError,
    UpgradeError,
    confirm_values,
    consume_grant,
    current_code,
    is_locked,
    issue_grant,
    lock_user,
    record_event,
    record_policy_denial,
    unlock_user,
    verify_totp,
)
from corridor.contracts import Provenance
from corridor.contracts.confirm import ConfirmationLevel as L
from corridor.contracts.policy import Decision, PolicyDecision
from corridor.ledger import Ledger, verify_chain
from corridor.policy import load_policy_config
from policy_helpers import make_transfer

CFG = load_policy_config()
NOW = datetime(2026, 10, 11, 12, 0, 5, tzinfo=UTC)
H, H2 = "a" * 64, "b" * 64
U = "usr_ramesh"


def sess(engine: Engine) -> Session:
    return Session(engine)


def issue(s: Session, h: str = H, user: str = U, level: L = L.CONFIRM, required: L = L.CONFIRM):
    return issue_grant(
        s, proposal_hash=h, user_id=user, level=level, required=required, issued_at=NOW, now=NOW
    )


def consume(s: Session, g, *, h: str = H, user: str = U, minimum: L = L.CONFIRM, now=NOW):  # type: ignore[no-untyped-def]
    return consume_grant(
        s, grant_id=g.grant_id, proposal_hash=h, user_id=user, minimum=minimum, now=now
    )


def test_grant_is_single_use(sandbox: Engine) -> None:
    with sess(sandbox) as s, s.begin():
        g = issue(s)
        assert consume(s, g).used is True
        with pytest.raises(GrantError) as e:
            consume(s, g)
    assert e.value.code == "already_used"


def test_swap_attack_a_grant_for_one_hash_cannot_run_another(sandbox: Engine) -> None:
    with sess(sandbox) as s, s.begin():
        g = issue(s, H)
        with pytest.raises(GrantError) as e:
            consume(s, g, h=H2)
    assert e.value.code == "hash_mismatch"


def test_grant_expires_after_exactly_120_seconds(sandbox: Engine) -> None:
    with sess(sandbox) as s, s.begin():
        g = issue(s)
        with pytest.raises(GrantError) as e:
            consume(s, g, now=NOW + timedelta(seconds=120))
        assert e.value.code == "expired"
        assert consume(s, g, now=NOW + timedelta(seconds=119)).used


def test_grant_cannot_be_used_by_another_user(sandbox: Engine) -> None:
    with sess(sandbox) as s, s.begin():
        g = issue(s)
        with pytest.raises(GrantError) as e:
            consume(s, g, user="usr_priya")
    assert e.value.code == "wrong_user"


def test_unknown_grant_and_levels(sandbox: Engine) -> None:
    with sess(sandbox) as s, s.begin():
        with pytest.raises(GrantError) as e:
            consume_grant(
                s, grant_id="nope", proposal_hash=H, user_id=U, minimum=L.CONFIRM, now=NOW
            )
        assert e.value.code == "not_found"
        g = issue(s, level=L.CONFIRM)
        with pytest.raises(GrantError) as e:
            consume(s, g, minimum=L.MFA)
        assert e.value.code == "level_too_low"
        mfa = issue(s, level=L.MFA, required=L.MFA)
        assert consume(s, mfa, minimum=L.CONFIRM).level is L.MFA  # a stronger grant is enough


def test_cannot_issue_in_the_future_or_below_the_required_level(sandbox: Engine) -> None:
    with sess(sandbox) as s, s.begin():
        with pytest.raises(GrantError) as e:
            issue_grant(
                s, proposal_hash=H, user_id=U, level=L.CONFIRM, required=L.CONFIRM,
                issued_at=NOW + timedelta(seconds=1), now=NOW,
            )  # fmt: skip
        assert e.value.code == "issued_in_future"
        with pytest.raises(GrantError) as e:
            issue(s, level=L.CONFIRM, required=L.MFA)
        assert e.value.code == "level_too_low"


def test_concurrent_consumption_lets_exactly_one_through(sandbox: Engine) -> None:
    with sess(sandbox) as s, s.begin():
        g = issue(s)
    results: list[str] = []

    def run() -> None:
        try:
            with sess(sandbox) as s2, s2.begin():
                consume(s2, g)
            results.append("ok")
        except GrantError as exc:
            results.append(exc.code)

    threads = [threading.Thread(target=run) for _ in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("ok") == 1 and results.count("already_used") == 9


def test_totp_accepts_the_current_code_once_and_rejects_replay_and_garbage(sandbox: Engine) -> None:
    sd = sandbox.secrets_dir  # type: ignore[attr-defined]
    code = current_code(U, NOW, sd)
    with sess(sandbox) as s, s.begin():
        assert verify_totp(s, U, code, NOW, CFG, sd) == (True, None)
        assert verify_totp(s, U, code, NOW, CFG, sd)[0] is False  # replay
        assert verify_totp(s, U, "abc", NOW, CFG, sd)[0] is False
        assert verify_totp(s, U, None, NOW, CFG, sd)[0] is False
        assert (
            verify_totp(s, U, "000000" if code != "000000" else "111111", NOW, CFG, sd)[0] is False
        )


def test_totp_window_is_one_step(sandbox: Engine) -> None:
    sd = sandbox.secrets_dir  # type: ignore[attr-defined]
    with sess(sandbox) as s, s.begin():
        near = current_code(U, NOW - timedelta(seconds=30), sd)
        assert verify_totp(s, U, near, NOW, CFG, sd)[0] is True
        far = current_code(U, NOW - timedelta(seconds=120), sd)
        assert verify_totp(s, U, far, NOW, CFG, sd)[0] is False


def test_five_wrong_codes_lock_and_unlock_needs_a_valid_code(sandbox: Engine) -> None:
    sd = sandbox.secrets_dir  # type: ignore[attr-defined]
    svc = ConfirmationService(CFG, Ledger(), sd)
    with sess(sandbox) as s, s.begin():
        reasons = [
            verify_totp(s, U, "000001", NOW + timedelta(seconds=i), CFG, sd)[1] for i in range(5)
        ]
        assert reasons[:4] == [None] * 4 and reasons[4] == "totp_failures"
        assert is_locked(s, U)
        bad = svc.unlock(s, user_id=U, totp="000001", now=NOW, trace_id="t", session_id=None)
        assert bad.ok is False and is_locked(s, U)
        cooling = svc.unlock(  # after 5 wrong codes there is a cooldown, even for the right code
            s, user_id=U, totp=current_code(U, NOW, sd), now=NOW + timedelta(seconds=40),
            trace_id="t", session_id=None,
        )  # fmt: skip
        assert cooling.reason == "unlock_cooldown" and is_locked(s, U)
        later = NOW + timedelta(minutes=11)
        good = svc.unlock(
            s, user_id=U, totp=current_code(U, later, sd), now=later, trace_id="t", session_id=None
        )
        assert good.ok and not is_locked(s, U)
        assert (
            svc.unlock(s, user_id=U, totp=None, now=NOW, trace_id="t", session_id=None).reason
            == "not_locked"
        )
        # old failures do not count after the unlock
        assert verify_totp(s, U, "000002", later + timedelta(seconds=1), CFG, sd)[1] is None
        assert not is_locked(s, U)


def test_three_denied_attempts_in_ten_minutes_lock(sandbox: Engine) -> None:
    with sess(sandbox) as s, s.begin():
        assert record_event(s, U, "denied", NOW, CFG) is None
        assert record_event(s, U, "failed_high_risk", NOW + timedelta(minutes=1), CFG) is None
        assert record_event(s, U, "denied", NOW + timedelta(minutes=2), CFG) == "denied_attempts"
        assert is_locked(s, U)
        assert record_event(s, U, "denied", NOW, CFG) is None  # already locked: no second lock


def test_attempts_outside_the_window_do_not_count(sandbox: Engine) -> None:
    with sess(sandbox) as s, s.begin():
        for i in range(3):
            assert record_event(s, U, "denied", NOW + timedelta(minutes=11 * i), CFG) is None
        assert not is_locked(s, U)


def test_one_critical_event_locks_at_once(sandbox: Engine) -> None:
    with sess(sandbox) as s, s.begin():
        assert record_event(s, U, "critical", NOW, CFG) == "critical_risk"
        lock_user(s, U, "again", NOW)  # idempotent
        unlock_user(s, U, NOW)
        assert not is_locked(s, U)
        unlock_user(s, U, NOW)  # unlocking an unlocked user is a no-op
        lock_user(s, U, "manual", NOW)
        assert is_locked(s, U)


def test_policy_denial_counting_only_counts_attack_related_rules(sandbox: Engine) -> None:
    def deny(rule_ids, reasons=()):  # type: ignore[no-untyped-def]
        return PolicyDecision(
            decision=Decision.DENY, rule_ids=tuple(rule_ids), reasons=tuple(reasons),
            policy_version_hash="c" * 64,
        )  # fmt: skip

    with sess(sandbox) as s, s.begin():
        for _ in range(5):  # insufficient funds is not an attack
            assert record_policy_denial(s, U, deny(["R-BALANCE"]), NOW, CFG) is None
        confirm = PolicyDecision(
            decision=Decision.REQUIRE_MFA, rule_ids=("R-TAINT-PAYEE",), policy_version_hash="c" * 64
        )
        assert record_policy_denial(s, U, confirm, NOW, CFG) is None
        assert not is_locked(s, U)
        assert (
            record_policy_denial(s, U, deny(["R-RISK"], ["R-RISK.risk_critical"]), NOW, CFG)
            == "critical_risk"
        )


def test_service_confirm_flow_with_ledger_entries(sandbox: Engine) -> None:
    sd = sandbox.secrets_dir  # type: ignore[attr-defined]
    svc = ConfirmationService(CFG, Ledger(), sd)

    def go(s: Session, required: L, totp: str | None, now=NOW):  # type: ignore[no-untyped-def]
        return svc.confirm(
            s, user_id=U, proposal_hash=H, required=required, totp=totp, now=now, trace_id="t",
            session_id="sess",
        )  # fmt: skip

    with sess(sandbox) as s, s.begin():
        ok = go(s, L.CONFIRM, None)
        assert ok.ok and ok.grant is not None and ok.grant.level is L.CONFIRM
        assert go(s, L.MFA, None).reason == "mfa_required"
        wrong = go(s, L.MFA, "000001")
        assert wrong.reason == "mfa_failed" and wrong.attempts_left == 4 and not wrong.locked
        good = go(s, L.MFA, current_code(U, NOW, sd))
        assert good.ok and good.grant is not None and good.grant.level is L.MFA
        for i in range(4):  # the 5th wrong code in the window locks
            last = go(s, L.MFA, "000009", NOW + timedelta(seconds=60 + i))
        assert last.locked and last.reason == "mfa_failed"
        assert go(s, L.CONFIRM, None, NOW + timedelta(seconds=90)).reason == "locked"
        assert verify_chain(s).valid  # every attempt and the lock were ledgered
        kinds = [
            r[0] for r in s.execute(text("SELECT event_type FROM ledger_entries ORDER BY seq"))
        ]
    assert {"confirmation_granted", "confirmation_rejected", "mfa_attempt", "safety_lock"} <= set(
        kinds
    )


def test_upgrade_to_user_confirmed_needs_a_consumed_grant_for_the_exact_proposal(
    sandbox: Engine,
) -> None:
    p = make_transfer(payee_prov=Provenance.UNTRUSTED).model_copy(update={"user_id": U})
    h = p.canonical_hash()
    with sess(sandbox) as s, s.begin():
        g = issue(s, h)
        with pytest.raises(UpgradeError):
            confirm_values(p, g, NOW)  # not consumed yet
        used = consume(s, g, h=h)
    out = confirm_values(p, used, NOW)
    assert out.payee_id is not None and out.payee_id.provenance is Provenance.USER_CONFIRMED
    assert out.source_account_id.provenance is Provenance.SYSTEM  # bank records stay system
    assert out.canonical_hash() == h  # provenance is not part of the hash
    other = make_transfer(5_001).model_copy(update={"user_id": U})
    with pytest.raises(UpgradeError):
        confirm_values(other, used, NOW)  # a different proposal
    with pytest.raises(UpgradeError):
        confirm_values(p.model_copy(update={"user_id": "someone"}), used, NOW)
    with pytest.raises(UpgradeError):
        confirm_values(p, used, NOW + timedelta(seconds=200))  # expired
    no_payee = p.model_copy(update={"payee_id": None, "amount": None})
    with sess(sandbox) as s, s.begin():
        g2 = issue(s, no_payee.canonical_hash())
        used2 = consume(s, g2, h=no_payee.canonical_hash())
    assert confirm_values(no_payee, used2, NOW).payee_id is None


# ---- documents cannot lock a user (ADR 0006, P2-3) ---------------------------------------------------


def _deny(rule: str = "R-TAINT-PAYEE") -> PolicyDecision:
    return PolicyDecision(
        decision=Decision.DENY,
        rule_ids=(rule,),
        reasons=(f"{rule}.x",),
        policy_version_hash="c" * 64,
    )


def test_denials_caused_by_documents_count_once_per_hash_and_at_most_once_in_total(
    sandbox: Engine,
) -> None:
    docs = [f"{i:064x}" for i in range(1, 6)]
    with sess(sandbox) as s, s.begin():
        for h in docs:  # five different poisoned documents, the user does nothing else
            assert record_policy_denial(s, U, _deny(), NOW, CFG, (h,)) is None
        assert not is_locked(s, U)
        # the same document again is not even recorded a second time
        before = s.execute(
            text("SELECT count(*) FROM security_events WHERE kind = 'doc_denied'")
        ).scalar_one()
        record_policy_denial(s, U, _deny(), NOW, CFG, (docs[0],))
        after = s.execute(
            text("SELECT count(*) FROM security_events WHERE kind = 'doc_denied'")
        ).scalar_one()
        assert before == after == 5


def test_the_users_own_attempts_still_lock_and_documents_add_at_most_one(sandbox: Engine) -> None:
    with sess(sandbox) as s, s.begin():
        assert record_event(s, U, "failed_high_risk", NOW, CFG) is None  # the user's own
        assert (
            record_policy_denial(s, U, _deny(), NOW, CFG, ("a" * 64,)) is None
        )  # one from a document
        assert (
            record_policy_denial(s, U, _deny(), NOW, CFG, ("b" * 64,)) is None
        )  # a second adds nothing
        assert not is_locked(s, U)
        assert (
            record_event(s, U, "failed_high_risk", NOW, CFG) == "denied_attempts"
        )  # own + own + 1 doc


def test_a_critical_risk_score_on_a_document_proposal_does_not_lock_at_once(
    sandbox: Engine,
) -> None:
    """P3-1 (ADR 0006 amendment): a poisoned document that scores critical counts as one document denial,
    so a third party cannot lock a user with it, while the user's own critical request still locks."""
    crit = PolicyDecision(
        decision=Decision.DENY, rule_ids=("R-RISK",), reasons=("R-RISK.risk_critical",),
        policy_version_hash="c" * 64,
    )  # fmt: skip
    with sess(sandbox) as s, s.begin():
        for h in ("a" * 64, "b" * 64, "c" * 64):
            assert record_policy_denial(s, U, crit, NOW, CFG, (h,)) is None
        assert not is_locked(s, U)
        assert record_policy_denial(s, U, crit, NOW, CFG, ()) == "critical_risk"
        assert is_locked(s, U)
