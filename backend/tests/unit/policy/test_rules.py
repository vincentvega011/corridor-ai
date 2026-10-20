"""Rule by rule tests of the deterministic policy engine (docs/spec/modules.md 5.7)."""

from datetime import timedelta
from decimal import Decimal

import pytest

from corridor.contracts import ActionType, CurrencyCode, Money, Provenance, Tainted
from corridor.contracts.policy import Decision
from corridor.policy import (
    AccountInfo,
    InvoiceInfo,
    PayeeInfo,
    evaluate,
    evaluate_read,
    parse_policy,
)
from corridor.policy.rules import RuleResult, to_npr_minor
from policy_helpers import (
    AUD,
    CFG,
    NOW,
    NPR,
    SYSTEM,
    TYPED,
    make_other,
    make_state,
    make_transfer,
    risk,
    tainted,
)  # fmt: skip

D = Decision


def ev(p, s=None, **kw):  # type: ignore[no-untyped-def]
    return evaluate(p, s or make_state(), CFG, NOW, **kw)


def has(d, rule: str, key: str | None = None) -> bool:  # type: ignore[no-untyped-def]
    return rule in d.rule_ids and (key is None or f"{rule}.{key}" in d.reasons)


def test_small_transfer_to_verified_payee_needs_confirm() -> None:
    d = ev(make_transfer())
    assert d.decision is D.REQUIRE_CONFIRM
    assert has(d, "R-TIER-LOW", "within_low_limit") and "R-FLOOR" in d.rule_ids
    assert d.policy_version_hash == CFG.version_hash


def test_amount_at_the_limit_is_low_and_one_cent_above_is_high() -> None:
    # AUD 277.77 x 90 = NPR 24,999.30 ; AUD 277.78 x 90 = NPR 25,000.20 (low limit NPR 25,000.00)
    assert ev(make_transfer(27_777)).decision is D.REQUIRE_CONFIRM
    d = ev(make_transfer(27_778))
    assert d.decision is D.REQUIRE_MFA and has(d, "R-TIER-HIGH", "amount_above_low_limit")


def test_conversion_rounds_up_so_limits_are_never_under_counted() -> None:
    s = make_state(fx_to_npr={NPR: Decimal(1), AUD: Decimal("90.001")})
    assert to_npr_minor(Money(amount_minor=1, currency=AUD), s) == 91  # 90.001 rounds up
    assert to_npr_minor(Money(amount_minor=100, currency=CurrencyCode.USD), s) is None


def test_new_and_unverified_payees_need_mfa() -> None:
    new = PayeeInfo(
        currency=AUD, verified=True, created_at=NOW - timedelta(days=29), provenance=SYSTEM
    )
    d = ev(make_transfer(), make_state(payees={"payee1": new}))
    assert d.decision is D.REQUIRE_MFA and has(d, "R-TIER-HIGH", "new_payee")
    unverified = PayeeInfo(
        currency=AUD, verified=False, created_at=NOW - timedelta(days=60), provenance=SYSTEM
    )
    d = ev(make_transfer(), make_state(payees={"payee1": unverified}))
    assert d.decision is D.REQUIRE_MFA and has(d, "R-TIER-HIGH", "unverified_payee")


def test_payee_exactly_at_the_age_limit_is_not_new() -> None:
    p = PayeeInfo(
        currency=AUD, verified=True, created_at=NOW - timedelta(days=30), provenance=SYSTEM
    )
    assert ev(make_transfer(), make_state(payees={"payee1": p})).decision is D.REQUIRE_CONFIRM


def test_schedule_needs_mfa() -> None:
    d = ev(make_other(ActionType.SCHEDULE))
    assert d.decision is D.REQUIRE_MFA and has(d, "R-TIER-HIGH", "schedule")


@pytest.mark.parametrize("prov", [Provenance.UNTRUSTED, Provenance.MODEL_INFERRED])
def test_tainted_payee_needs_mfa_even_when_the_payee_is_stored_as_trusted(prov: Provenance) -> None:
    d = ev(make_transfer(payee_prov=prov))
    assert d.decision is D.REQUIRE_MFA and has(d, "R-TAINT-PAYEE", "tainted_payee")


def test_stored_taint_lowers_trust_even_if_the_proposal_claims_system() -> None:
    s = make_state(
        payees={
            "payee1": PayeeInfo(currency=AUD, verified=True, created_at=NOW - timedelta(days=90), provenance=Provenance.UNTRUSTED)
        }
    )  # fmt: skip
    d = ev(make_transfer(payee_prov=SYSTEM), s)
    assert d.decision is D.REQUIRE_MFA and has(d, "R-TAINT-PAYEE", "tainted_payee")


def test_payee_missing_from_state_uses_the_proposal_provenance() -> None:
    d = ev(make_transfer(payee="ghost", payee_prov=Provenance.UNTRUSTED))
    assert d.decision is D.DENY and has(d, "R-OWNERSHIP", "payee_not_found")


def test_tainted_source_account_needs_mfa() -> None:
    d = ev(make_transfer(account_prov=Provenance.MODEL_INFERRED))
    assert d.decision is D.REQUIRE_MFA and has(d, "R-TAINT-PAYEE", "tainted_source_account")


def test_untrusted_amount_needs_confirm_and_inferred_amount_needs_mfa() -> None:
    d = ev(make_transfer(amount_prov=Provenance.UNTRUSTED))
    assert has(d, "R-TAINT-AMOUNT", "untrusted_amount") and d.decision is D.REQUIRE_CONFIRM
    assert "R-TIER-LOW" not in d.rule_ids  # a tainted amount never gets the plain low tier
    d = ev(make_transfer(amount_prov=Provenance.MODEL_INFERRED))
    assert d.decision is D.REQUIRE_MFA and has(d, "R-TAINT-AMOUNT", "inferred_amount")


def test_confirmed_value_is_treated_as_typed_for_amounts() -> None:
    assert ev(make_transfer(amount_prov=Provenance.USER_CONFIRMED)).decision is D.REQUIRE_CONFIRM


def invoice_state(**inv: object):  # type: ignore[no-untyped-def]
    info = {
        "beneficiary_payee_id": "payee1", "amount_minor": 5_000, "currency": AUD,
        "provenance": SYSTEM,
    }  # fmt: skip
    info.update(inv)
    return make_state(invoices={"inv1": InvoiceInfo(**info)})  # type: ignore[arg-type]


def make_invoice_payment(**over):  # type: ignore[no-untyped-def]
    return make_transfer(action=ActionType.PAY_INVOICE, invoice_id="inv1", **over)


def test_invoice_paid_to_its_stored_beneficiary_is_a_normal_confirm() -> None:
    d = ev(make_invoice_payment(), invoice_state())
    assert d.decision is D.REQUIRE_CONFIRM


def test_invoice_to_a_different_payee_is_denied_and_tainted_adds_taint_rule() -> None:
    s = invoice_state(beneficiary_payee_id="payee_npr")
    d = ev(make_invoice_payment(), s)
    assert d.decision is D.DENY and has(d, "R-INVOICE-BENEFICIARY", "beneficiary_differs")
    assert "R-TAINT-PAYEE" not in d.rule_ids
    d = ev(make_invoice_payment(payee_prov=Provenance.UNTRUSTED), s)
    assert d.decision is D.DENY and has(d, "R-TAINT-PAYEE", "tainted_beneficiary_differs")


def test_an_instalment_is_allowed_but_never_more_than_is_still_owed() -> None:
    # the default proposal pays 5,000: a larger invoice makes it an instalment (P3-1, ADR 0014)
    assert (
        ev(make_invoice_payment(), invoice_state(amount_minor=6_000)).decision is D.REQUIRE_CONFIRM
    )
    # more than the invoice, or more than what is left after earlier instalments, is denied
    for inv in ({"amount_minor": 4_000}, {"amount_minor": 6_000, "paid_minor": 2_000}):
        d = ev(make_invoice_payment(), invoice_state(**inv))
        assert d.decision is D.DENY and has(d, "R-INVOICE-BENEFICIARY", "invoice_amount_differs")
    d = ev(make_invoice_payment(), invoice_state(currency=CurrencyCode.USD))
    assert d.decision is D.DENY and has(d, "R-INVOICE-BENEFICIARY", "invoice_amount_differs")


def test_invoice_must_exist() -> None:
    d = ev(make_invoice_payment(), make_state())
    assert d.decision is D.DENY and has(d, "R-OWNERSHIP", "invoice_not_found")
    p = make_invoice_payment().model_copy(update={"invoice_id": None})
    assert has(ev(p), "R-OWNERSHIP", "invoice_not_found")


def test_untrusted_invoice_taints_the_amount() -> None:
    d = ev(make_invoice_payment(), invoice_state(provenance=Provenance.UNTRUSTED))
    assert has(d, "R-TAINT-AMOUNT", "untrusted_amount")


def test_missing_amount_is_handled_by_every_rule() -> None:
    p = make_transfer().model_copy(update={"amount": None})
    d = ev(p)
    assert d.decision in (D.DENY, D.REQUIRE_MFA, D.REQUIRE_CONFIRM)
    p = make_invoice_payment().model_copy(update={"amount": None})
    assert has(ev(p, invoice_state()), "R-INVOICE-BENEFICIARY", "invoice_amount_differs")


def test_ownership_rules() -> None:
    s = make_state(
        accounts={"acc1": AccountInfo(currency=AUD, balance_minor=1_000_000, active=False)}
    )
    assert has(ev(make_transfer(), s), "R-OWNERSHIP", "account_not_usable")
    p = make_transfer().model_copy(update={"source_account_id": tainted("acc_other")})
    assert has(ev(p), "R-OWNERSHIP", "account_not_usable")
    p = make_transfer().model_copy(update={"payee_id": None})
    assert has(ev(p), "R-OWNERSHIP", "payee_not_found")
    cancel = make_other(ActionType.CANCEL_SCHEDULE, source_account_id=tainted("acc_other"))
    assert ev(cancel).decision is D.DENY


def test_currency_rules() -> None:
    s = make_state(accounts={"acc1": AccountInfo(currency=CurrencyCode.USD, balance_minor=10**9)})
    assert has(ev(make_transfer(), s), "R-CURRENCY", "account_currency_mismatch")
    d = ev(make_transfer(payee="payee_npr"), make_state(supported_pairs=frozenset()))
    assert has(d, "R-CURRENCY", "corridor_unsupported")
    d = ev(make_transfer(payee="payee_npr"))  # AUD to NPR needs a route
    assert has(d, "R-CURRENCY", "route_missing")
    s = make_state(fx_to_npr={NPR: Decimal(1)})
    assert has(ev(make_transfer(), s), "R-CURRENCY", "no_fx_rate")


def test_route_must_deliver_in_the_payee_currency() -> None:
    from contract_builders import make_route

    route = make_route()  # delivers NPR
    ok = make_transfer(100_000, payee="payee_npr", route=route)
    assert "R-CURRENCY" not in ev(ok).rule_ids
    bad = make_transfer(100_000, route=route)  # payee1 is an AUD payee
    assert has(ev(bad), "R-CURRENCY", "route_currency_mismatch")


def test_convert_needs_a_known_pair() -> None:
    conv = make_transfer(action=ActionType.CONVERT, payee_id=None, target_currency=CurrencyCode.NPR)
    assert ev(conv).decision is D.REQUIRE_CONFIRM
    d = ev(conv, make_state(convert_pairs=frozenset()))
    assert has(d, "R-CURRENCY", "pair_unsupported")
    # a convert whose target is missing is refused by the contract, so build it around validation
    broken = conv.model_copy(update={"target_currency": None})
    assert has(ev(broken), "R-CURRENCY", "pair_unsupported")


def test_balance_rule() -> None:
    s = make_state(accounts={"acc1": AccountInfo(currency=AUD, balance_minor=4_999)})
    d = ev(make_transfer(5_000), s)
    assert d.decision is D.DENY and has(d, "R-BALANCE", "insufficient_funds")
    assert "R-BALANCE" not in ev(make_other(ActionType.SCHEDULE), s).rule_ids  # no debit now


def test_daily_limit_counts_today_in_npr_equivalent() -> None:
    # limit NPR 1,000,000 = AUD 11,111.12 at 90. Earlier today: AUD 5,000 (NPR 450,000).
    earlier = (Money(amount_minor=500_000, currency=AUD),)
    s = make_state(
        outgoing_today=earlier, accounts={"acc1": AccountInfo(currency=AUD, balance_minor=10**9)}
    )
    assert "R-DAILY-LIMIT" not in ev(make_transfer(500_000), s).rule_ids  # 900,000 in total
    d = ev(make_transfer(611_112), s)  # 450,000 + 550,000.8 > 1,000,000
    assert d.decision is D.DENY and has(d, "R-DAILY-LIMIT", "daily_limit_exceeded")
    # a missing rate for an earlier transfer cannot be summed
    s2 = make_state(outgoing_today=(Money(amount_minor=1, currency=CurrencyCode.USD),))
    d2 = ev(make_transfer(), s2)  # an unknown earlier total cannot be checked: deny, never skip
    assert d2.decision is D.DENY and has(d2, "R-DAILY-LIMIT", "daily_total_unknown")
    assert "R-DAILY-LIMIT" not in ev(make_other(ActionType.CANCEL_SCHEDULE)).rule_ids


def test_velocity_mfa_then_deny_and_old_transfers_do_not_count() -> None:
    def times(n: int, minutes_ago: int = 1):  # type: ignore[no-untyped-def]
        return tuple(NOW - timedelta(minutes=minutes_ago, seconds=i) for i in range(n))

    # this transfer counts too: "more than 3 in 10 minutes" fires on the 4th, DENY on the 7th
    assert (
        "R-VELOCITY" not in ev(make_transfer(), make_state(recent_transfer_times=times(2))).rule_ids
    )
    d = ev(make_transfer(), make_state(recent_transfer_times=times(3)))
    assert d.decision is D.REQUIRE_MFA and has(d, "R-VELOCITY", "velocity_mfa")
    assert ev(make_transfer(), make_state(recent_transfer_times=times(5))).decision is D.REQUIRE_MFA
    d = ev(make_transfer(), make_state(recent_transfer_times=times(6)))
    assert d.decision is D.DENY and has(d, "R-VELOCITY", "velocity_deny")
    old = make_state(recent_transfer_times=times(9, minutes_ago=11))
    assert "R-VELOCITY" not in ev(make_transfer(), old).rule_ids
    assert (
        "R-VELOCITY"
        not in ev(
            make_other(ActionType.SCHEDULE), make_state(recent_transfer_times=times(9))
        ).rule_ids
    )


def test_lock_denies_outgoing_and_add_payee_but_not_harmless_changes() -> None:
    s = make_state(locked=True)
    for action in (ActionType.TRANSFER, ActionType.SCHEDULE, ActionType.ADD_PAYEE):
        p = make_transfer() if action is ActionType.TRANSFER else make_other(action)
        assert ev(p, s).decision is D.DENY and has(ev(p, s), "R-LOCK", "locked")
    assert ev(make_other(ActionType.CANCEL_SCHEDULE), s).decision is D.REQUIRE_CONFIRM
    # issuing a passport only creates something the user holds; sharing sends data out, so the lock applies
    assert ev(make_other(ActionType.SHARE_PASSPORT), s).decision is D.DENY


def test_injection_flagged_document_needs_mfa() -> None:
    h = "a" * 64
    s = make_state(flagged_docs=frozenset({h}))
    assert has(ev(make_transfer(derived_from_docs=(h,)), s), "R-INJECTION", "injection_suspected")
    assert "R-INJECTION" not in ev(make_transfer(derived_from_docs=("b" * 64,)), s).rule_ids


def test_risk_thresholds() -> None:
    assert "R-RISK" not in ev(make_transfer(risk=risk(5_999))).rule_ids
    d = ev(make_transfer(risk=risk(6_000)))
    assert d.decision is D.REQUIRE_MFA and has(d, "R-RISK", "risk_high")
    d = ev(make_transfer(risk=risk(9_000)))
    assert d.decision is D.DENY and has(d, "R-RISK", "risk_critical")


def test_payee_add_always_needs_mfa_and_never_from_untrusted_data() -> None:
    d = ev(make_other(ActionType.ADD_PAYEE))
    assert d.decision is D.REQUIRE_MFA and has(d, "R-PAYEE-ADD", "payee_add_mfa")
    from corridor.contracts.action import NewPayeeSpec

    for prov in (Provenance.UNTRUSTED, Provenance.MODEL_INFERRED):
        spec = NewPayeeSpec(
            display_name=tainted("Sam", TYPED), region=tainted("AU", TYPED), currency=AUD,
            account_no=tainted("123456", prov),
        )  # fmt: skip
        d = ev(make_other(ActionType.ADD_PAYEE, new_payee=spec))
        # needs MFA and carries the scam warning; the payment cap (R-PAYEE-COOLOFF) does the blocking
        assert d.decision is D.REQUIRE_MFA and has(d, "R-PAYEE-ADD", "payee_add_untrusted_warning")
    broken = make_other(ActionType.ADD_PAYEE).model_copy(update={"new_payee": None})
    assert ev(broken).decision is D.DENY


def test_settings_changes_through_a_budget_are_denied() -> None:
    from corridor.contracts.action import BudgetSpec

    bad = make_other(
        ActionType.SET_BUDGET, budget=BudgetSpec(category="Daily LIMIT", limit=Money(amount_minor=1, currency=AUD))
    )  # fmt: skip
    assert ev(bad).decision is D.DENY and has(ev(bad), "R-SETTINGS", "settings_change")
    d = ev(make_other(ActionType.SET_BUDGET))
    assert d.decision is D.REQUIRE_CONFIRM and has(d, "R-TIER-LOW", "state_change")
    no_budget = make_other(ActionType.SET_BUDGET).model_copy(update={"budget": None})
    assert "R-SETTINGS" not in ev(no_budget).rule_ids


def test_non_money_actions_need_a_confirmation() -> None:
    d = ev(make_other(ActionType.CANCEL_SCHEDULE))
    assert d.decision is D.REQUIRE_CONFIRM and has(d, "R-TIER-LOW", "state_change")
    d = ev(make_other(ActionType.ISSUE_PASSPORT), make_state(credit_event_count=3))
    assert d.decision is D.REQUIRE_CONFIRM and has(d, "R-TIER-LOW", "state_change")


def test_unknown_action_is_denied() -> None:
    p = make_transfer().model_copy(update={"action": "wire_everything"})
    d = ev(p)
    assert d.decision is D.DENY and d.rule_ids == ("R-UNKNOWN",)


def test_reasons_show_only_the_deciding_rank_for_a_denial() -> None:
    d = ev(
        make_transfer(5_000),
        make_state(accounts={"acc1": AccountInfo(currency=AUD, balance_minor=1)}),
    )
    assert d.decision is D.DENY and d.reasons == ("R-BALANCE.insufficient_funds",)
    assert "R-FLOOR" in d.rule_ids  # the audit trail still lists every rule that fired


def test_read_tools() -> None:
    assert evaluate_read("get_balances", CFG).decision is D.ALLOW
    d = evaluate_read("transfer_money", CFG)
    assert d.decision is D.DENY and d.rule_ids == ("R-TIER-READ",)


def test_extra_rule_hook_can_only_tighten() -> None:
    deny = lambda p, s, c, n: [RuleResult("R-TEST", D.DENY, "x")]  # noqa: E731
    assert ev(make_transfer(), extra_rules=(deny,)).decision is D.DENY


def test_policy_hash_changes_with_the_config() -> None:
    raw = {
        "version": 1, "base_currency": "NPR", "limits": {"low_npr_minor": 1, "daily_npr_minor": 2},
        "new_payee_days": 7, "payee_cooloff": {"hours": 24, "first_payment_cap_npr_minor": 500000, "untrusted_origin_cap_npr_minor": 0},
        "velocity": {"window_minutes": 10, "mfa_over": 3, "deny_over": 6},
        "risk": {"high_bps": 6000, "critical_bps": 9000},
        "lock": {"window_minutes": 10, "denied_attempts": 3, "totp_failures": 5},
        "protected_setting_words": ["limit"], "read_tools": ["get_balances"],
    }  # fmt: skip
    a = parse_policy(raw)
    b = parse_policy({**raw, "limits": {"low_npr_minor": 2, "daily_npr_minor": 2}})
    assert a.version_hash != b.version_hash and a.version_hash == parse_policy(raw).version_hash
    assert (
        CFG.version_hash
        == __import__("corridor.policy", fromlist=["x"]).load_policy_config().version_hash
    )


def test_decision_has_no_tainted_wrapper_leak() -> None:
    d = ev(make_transfer())
    assert isinstance(d.reasons[0], str) and not isinstance(d, Tainted)


# ----------------------------------------------------------------------------- R-PAYEE-COOLOFF
def fresh(
    hours: float, key: str = "k1", prov: Provenance = SYSTEM, verified: bool = False
) -> PayeeInfo:
    return PayeeInfo(
        currency=AUD, verified=verified, created_at=NOW - timedelta(hours=hours), provenance=prov,
        account_key=key,
    )  # fmt: skip


def test_cooloff_denies_above_the_cap_with_the_earliest_time_whatever_the_provenance() -> None:
    # cap NPR 5,000.00 = AUD 55.55 at 90. AUD 55.56 is over, AUD 55.55 is not.
    s = make_state(payees={"payee1": fresh(1)})
    assert not has(ev(make_transfer(5_555), s), "R-PAYEE-COOLOFF")
    d = ev(make_transfer(5_556), s)
    assert d.decision is D.DENY and has(d, "R-PAYEE-COOLOFF")
    until = (NOW + timedelta(hours=23)).isoformat(timespec="minutes")
    assert f"R-PAYEE-COOLOFF.first_payment_cap@{until}|500000" in d.reasons
    u = make_state(payees={"payee1": fresh(1, prov=Provenance.UNTRUSTED)})
    assert any("first_payment_cap_untrusted@" in r for r in ev(make_transfer(5_556), u).reasons)


def test_cooloff_holds_an_untrusted_origin_account_in_full_but_not_one_the_user_typed() -> None:
    # Part C: cap 0 for UNTRUSTED or MODEL_INFERRED origin, even for AUD 0.01; typed accounts keep the cap
    for prov in (Provenance.UNTRUSTED, Provenance.MODEL_INFERRED):
        d = ev(make_transfer(1), make_state(payees={"payee1": fresh(1, prov=prov)}))
        assert d.decision is D.DENY and has(d, "R-PAYEE-COOLOFF")
        until = (NOW + timedelta(hours=23)).isoformat(timespec="minutes")
        assert f"R-PAYEE-COOLOFF.first_payment_cap_untrusted@{until}|0" in d.reasons
    typed = make_state(payees={"payee1": fresh(1, prov=Provenance.USER_TYPED)})
    assert not has(ev(make_transfer(5_555), typed), "R-PAYEE-COOLOFF")
    assert has(ev(make_transfer(5_556), typed), "R-PAYEE-COOLOFF")
    # one untrusted row makes the whole account untrusted (a re-save under another name does not launder it)
    mixed = make_state(
        payees={
            "payee1": fresh(1, "k9", Provenance.USER_TYPED),
            "alias": fresh(2, "k9", Provenance.UNTRUSTED),
        }
    )
    assert has(ev(make_transfer(1), mixed), "R-PAYEE-COOLOFF")
    # after 24 hours the hold ends
    assert not has(
        ev(
            make_transfer(1),
            make_state(payees={"payee1": fresh(24, prov=Provenance.UNTRUSTED, verified=True)}),
        ),
        "R-PAYEE-COOLOFF",
    )


def test_cooloff_ends_exactly_24_hours_after_the_account_was_first_saved() -> None:
    assert has(
        ev(make_transfer(500_000), make_state(payees={"payee1": fresh(23.99)})), "R-PAYEE-COOLOFF"
    )
    assert not has(
        ev(make_transfer(500_000), make_state(payees={"payee1": fresh(24)})), "R-PAYEE-COOLOFF"
    )


def test_cooloff_counts_what_the_account_already_received_so_splitting_does_not_help() -> None:
    from corridor.contracts import Money

    s = make_state(
        payees={"payee1": fresh(1)},
        paid_to_account={"k1": (Money(amount_minor=5_000, currency=AUD),)},
    )
    assert not has(ev(make_transfer(500), s), "R-PAYEE-COOLOFF")  # 5,000 + 500 = AUD 55.00
    assert has(ev(make_transfer(556), s), "R-PAYEE-COOLOFF")  # AUD 55.56 in total


def test_cooloff_follows_the_account_not_the_name_or_the_row() -> None:
    # the same account saved again under a new name an hour ago, after an older row 5 hours ago
    s = make_state(
        payees={"payee1": fresh(1, "k9"), "alias": fresh(5, "k9"), "payee_npr": fresh(900, "k2")}
    )
    assert has(ev(make_transfer(5_556), s), "R-PAYEE-COOLOFF")
    # a re-added row of an account that is already older than 24 hours is not a new account
    old = make_state(payees={"payee1": fresh(0.1, "k9"), "alias": fresh(100, "k9")})
    assert not has(ev(make_transfer(500_000), old), "R-PAYEE-COOLOFF")


def test_cooloff_fails_closed_without_an_fx_rate_and_covers_invoices_and_schedules() -> None:
    s = make_state(payees={"payee1": fresh(1)}, fx_to_npr={NPR: Decimal(1)})
    assert has(ev(make_transfer(1), s), "R-PAYEE-COOLOFF")
    s = make_state(payees={"payee1": fresh(1)})
    assert has(
        ev(make_transfer(500_000, action=ActionType.PAY_INVOICE, invoice_id="inv1"), s),
        "R-PAYEE-COOLOFF",
    )
    assert not has(
        ev(make_other(ActionType.SCHEDULE), s), "R-PAYEE-COOLOFF"
    )  # AUD 1.00 is under the cap


def test_cooloff_does_not_touch_established_payees_or_other_actions() -> None:
    assert not has(ev(make_transfer(500_000)), "R-PAYEE-COOLOFF")
    assert not has(ev(make_other(ActionType.ADD_PAYEE)), "R-PAYEE-COOLOFF")
    assert not has(ev(make_transfer(payee="ghost")), "R-PAYEE-COOLOFF")


def test_cooloff_fails_closed_without_an_account_key_and_rejects_a_window_over_24_hours() -> None:
    from pydantic import ValidationError

    from corridor.policy.config import PayeeCooloff

    nokey = PayeeInfo(
        currency=AUD, verified=True, created_at=NOW - timedelta(hours=1), provenance=SYSTEM
    )
    assert has(ev(make_transfer(1), make_state(payees={"payee1": nokey})), "R-PAYEE-COOLOFF")
    with pytest.raises(ValidationError):
        PayeeCooloff(hours=25, first_payment_cap_npr_minor=1, untrusted_origin_cap_npr_minor=0)


# ---------------------------------------------------------------- R-CONTRIBUTION (P3-1)
def contribution_state(limit: int = 50_000, recent: int = 0):  # type: ignore[no-untyped-def]
    from corridor.policy.state import FamilyLinkInfo

    return make_state(
        family_links={
            "u2": FamilyLinkInfo(
                currency=CurrencyCode.QAR, per_request_limit_minor=limit, requests_last_24h=recent
            )
        },
        fx_to_npr={NPR: Decimal(1), AUD: Decimal("90"), CurrencyCode.QAR: Decimal("40")},
    )


def make_request(amount: int = 10_000, ccy: CurrencyCode = CurrencyCode.QAR, who: str = "u2"):  # type: ignore[no-untyped-def]
    from corridor.contracts.action import ContributionSpec

    return make_other(
        ActionType.REQUEST_CONTRIBUTION,
        amount=Tainted[Money](value=Money(amount_minor=amount, currency=ccy), provenance=Provenance.USER_TYPED),
        contribution=ContributionSpec(contributor_user_id=who, target_account_id="acc1"),
    )  # fmt: skip


def test_a_contribution_request_to_a_linked_member_within_the_limit_needs_only_a_confirmation() -> (
    None
):
    d = ev(make_request(), contribution_state())
    assert d.decision is D.REQUIRE_CONFIRM and "R-CONTRIBUTION" not in d.rule_ids


@pytest.mark.parametrize(
    ("kwargs", "key"),
    [
        ({"who": "u3"}, "no_family_link"),  # nobody linked: a request can never go to a stranger
        ({"amount": 50_001}, "contribution_over_limit"),
        ({"ccy": CurrencyCode.AUD}, "contribution_currency"),
    ],
)
def test_a_contribution_request_outside_the_link_is_denied(kwargs: dict, key: str) -> None:  # type: ignore[type-arg]
    d = ev(make_request(**kwargs), contribution_state())
    assert d.decision is D.DENY and has(d, "R-CONTRIBUTION", key)


@pytest.mark.parametrize("recent", [0, 1, 2])
def test_requests_below_the_daily_cap_per_contributor_are_allowed(recent: int) -> None:
    assert CFG.contribution.max_requests_per_contributor_per_day == 3
    d = ev(make_request(), contribution_state(recent=recent))
    assert d.decision is D.REQUIRE_CONFIRM and not has(
        d, "R-CONTRIBUTION", "contribution_daily_cap"
    )


@pytest.mark.parametrize("recent", [3, 4, 50])
def test_the_fourth_request_to_the_same_person_in_a_day_is_denied(recent: int) -> None:
    d = ev(make_request(), contribution_state(recent=recent))
    assert d.decision is D.DENY and has(d, "R-CONTRIBUTION", "contribution_daily_cap")


def test_a_contribution_request_into_someone_elses_account_is_denied() -> None:
    from corridor.contracts.action import ContributionSpec

    p = make_request().model_copy(
        update={
            "contribution": ContributionSpec(
                contributor_user_id="u2", target_account_id="acc_other"
            )
        }
    )
    d = ev(p, contribution_state())
    assert d.decision is D.DENY and has(d, "R-OWNERSHIP", "account_not_usable")


# ---------------------------------------------------------------- R-PASSPORT (P3-1)
def passport_state(**over):  # type: ignore[no-untyped-def]
    from corridor.policy.state import PassportInfo

    base = {"passports": {"pp1": PassportInfo(claim_ids=frozenset({"rent_on_time"}), expires_at=NOW + timedelta(days=30))},
            "credit_event_count": 3}  # fmt: skip
    base.update(over)
    return make_state(**base)


def test_issuing_a_passport_needs_payment_history_and_only_a_confirmation() -> None:
    p = make_other(ActionType.ISSUE_PASSPORT)
    assert ev(p, passport_state()).decision is D.REQUIRE_CONFIRM
    d = ev(p, passport_state(credit_event_count=0))
    assert d.decision is D.DENY and has(d, "R-PASSPORT", "no_payment_history")


def test_sharing_needs_mfa_a_held_claim_an_unexpired_passport_and_an_audience() -> None:
    p = make_other(ActionType.SHARE_PASSPORT)
    assert ev(p, passport_state()).decision is D.REQUIRE_MFA
    assert has(ev(p, make_state()), "R-PASSPORT", "passport_not_found")
    assert has(
        ev(
            p,
            passport_state(
                passports={
                    "pp1": __import__(
                        "corridor.policy.state", fromlist=["PassportInfo"]
                    ).PassportInfo(
                        claim_ids=frozenset({"tuition_on_time"}), expires_at=NOW + timedelta(days=1)
                    )
                }
            ),
        ),
        "R-PASSPORT",
        "claim_not_held",
    )
    assert has(
        ev(
            p,
            passport_state(
                passports={
                    "pp1": __import__(
                        "corridor.policy.state", fromlist=["PassportInfo"]
                    ).PassportInfo(
                        claim_ids=frozenset({"rent_on_time"}), expires_at=NOW - timedelta(seconds=1)
                    )
                }
            ),
        ),
        "R-PASSPORT",
        "passport_expired",
    )
