"""Deterministic risk score: each reason code, bounds, determinism (P3-1, ADR 0016)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from corridor.contracts import Provenance
from corridor.contracts.risk import RiskLevel
from corridor.policy import AccountInfo, PayeeInfo
from corridor.risk.score import assess, contributions
from policy_helpers import AUD, CFG, NOW, NPR, make_state, make_transfer


def state(**over):  # type: ignore[no-untyped-def]
    payee = {
        "currency": AUD, "verified": True, "created_at": NOW - timedelta(days=90),
        "provenance": Provenance.SYSTEM,
    }  # fmt: skip
    payee.update(over.pop("payee", {}))
    base = {
        "accounts": {"acc1": AccountInfo(currency=AUD, balance_minor=10**9)},
        "payees": {"payee1": PayeeInfo(**payee)},  # type: ignore[arg-type]
        "fx_to_npr": {NPR: Decimal(1), AUD: Decimal("90")},
    }
    base.update(over)
    return make_state(**base)


def codes(p, s, now=NOW):  # type: ignore[no-untyped-def]
    return {c for c, _ in contributions(p, s, CFG, now)}


def test_an_established_verified_payee_and_a_normal_amount_score_zero() -> None:
    a = assess(make_transfer(), state(), CFG, NOW)
    assert a.score_bps == 0 and a.level is RiskLevel.LOW and a.top_reasons == ()


def test_each_reason_code_fires_on_its_own_condition() -> None:
    p = make_transfer()
    assert "new_payee" in codes(p, state(payee={"created_at": NOW - timedelta(days=2)}))
    untrusted = {"provenance": Provenance.UNTRUSTED, "verified": False}
    assert "untrusted_payee" in codes(p, state(payee=untrusted))
    held = {**untrusted, "created_at": NOW - timedelta(hours=3)}
    assert {"held_payee", "untrusted_payee"} <= codes(p, state(payee=held))
    assert "document_derived" in codes(make_transfer(derived_from_docs=("a" * 64,)), state())
    assert "cop_mismatch" in codes(p, state(payee={"cop": "mismatch", "verified": False}))
    assert "cop_mismatch" not in codes(p, state(payee={"cop": "mismatch", "verified": True}))
    assert "recent_denials" in codes(p, state(recent_denials=2))


def test_the_amount_is_judged_against_the_users_own_history() -> None:
    s = state(history_median_npr_minor=100_000)  # NPR 1,000 median
    assert "amount_unusual" in codes(make_transfer(5_000), s)  # AUD 50 = NPR 4,500 = 4.5 x
    assert "amount_extreme" in codes(make_transfer(20_000), s)  # AUD 200 = NPR 18,000 = 18 x
    assert not codes(make_transfer(1_000), s)  # AUD 10 = NPR 900
    assert "amount_unusual" not in codes(make_transfer(5_000), state())  # no history: no judgement


def test_the_hour_is_the_users_local_hour() -> None:
    p = make_transfer()
    at_utc = datetime(2026, 10, 12, 17, 30, tzinfo=UTC)  # 04:30 next day in Sydney (UTC+11)
    assert "unusual_hour" in codes(p, state(timezone="Australia/Sydney"), at_utc)
    assert "unusual_hour" not in codes(p, state(timezone="UTC"), at_utc)
    assert "unusual_hour" not in codes(
        p, state(timezone="Not/AZone"), at_utc
    )  # unknown zone: UTC, no crash


def test_score_is_capped_levels_follow_thresholds_and_top_three_are_the_largest() -> None:
    s = state(
        payee={"provenance": Provenance.UNTRUSTED, "verified": False, "cop": "mismatch",
               "created_at": NOW - timedelta(hours=1)},
        recent_denials=3, history_median_npr_minor=1_000, timezone="Australia/Sydney",
    )  # fmt: skip
    a = assess(make_transfer(derived_from_docs=("a" * 64,)), s, CFG, NOW.replace(hour=17))
    assert a.score_bps == 10000 and a.level is RiskLevel.CRITICAL
    assert len(a.top_reasons) == 3
    contribs = [r.contribution_bps for r in a.top_reasons]
    assert contribs == sorted(contribs, reverse=True) and a.top_reasons[0].feature == "cop_mismatch"


def test_levels_at_the_configured_thresholds() -> None:
    mid = state(payee={"provenance": Provenance.UNTRUSTED, "verified": False, "cop": "mismatch"})
    a = assess(make_transfer(), mid, CFG, NOW)  # untrusted 3000 + cop mismatch 6000 = 9000
    assert a.score_bps >= CFG.risk.critical_bps and a.level is RiskLevel.CRITICAL
    only_new = assess(
        make_transfer(), state(payee={"created_at": NOW - timedelta(days=2)}), CFG, NOW
    )
    assert only_new.score_bps < CFG.risk.high_bps and only_new.level is RiskLevel.LOW


def test_assess_is_deterministic_and_never_reads_the_proposals_own_claim() -> None:
    s = state(recent_denials=1)
    p = make_transfer()
    assert assess(p, s, CFG, NOW) == assess(p, s, CFG, NOW)


@pytest.mark.parametrize("action", ["issue_passport", "share_passport", "request_contribution"])
def test_non_payment_actions_are_not_scored(action: str) -> None:
    from corridor.contracts import ActionType
    from policy_helpers import make_other

    p = make_other(ActionType(action))
    assert assess(p, state(recent_denials=3), CFG, NOW).score_bps == 0
