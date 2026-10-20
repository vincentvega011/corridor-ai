"""R-DUPLICATE (P2-6): the same payment to the same account within 24 hours needs a fresh step-up."""

from datetime import timedelta

from corridor.contracts import ActionType, Money
from corridor.contracts.policy import Decision as D
from corridor.policy import PaidItem, PayeeInfo, evaluate
from policy_helpers import AUD, CFG, NOW, SYSTEM, make_state, make_transfer

KEY = "acct1"


def state(*paid: tuple[float, int], payee_key: str = KEY, **kw):  # type: ignore[no-untyped-def]
    """A verified old payee with an account key and earlier payments (hours ago, amount minor)."""
    items = tuple(
        PaidItem(at=NOW - timedelta(hours=h), amount=Money(amount_minor=a, currency=AUD))
        for h, a in paid
    )
    payee = PayeeInfo(
        currency=AUD, verified=True, created_at=NOW - timedelta(days=90), provenance=SYSTEM,
        account_key=payee_key,
    )  # fmt: skip
    return make_state(
        payees={"payee1": payee},
        paid_to_account={KEY: tuple(i.amount for i in items)},
        paid_to_account_detail={KEY: items},
        **kw,
    )


def test_the_same_amount_to_the_same_account_within_24_hours_needs_mfa_and_names_the_earlier_payment() -> (
    None
):
    d = evaluate(make_transfer(5_000), state((2, 5_000)), CFG, NOW)
    assert d.decision is D.REQUIRE_MFA and "R-DUPLICATE" in d.rule_ids
    when = (NOW - timedelta(hours=2)).isoformat(timespec="minutes")
    assert f"R-DUPLICATE.same_payment@{when}|5000|AUD" in d.reasons


def test_it_is_a_step_up_never_a_denial() -> None:
    d = evaluate(make_transfer(5_000), state((0.01, 5_000)), CFG, NOW)
    assert d.decision is D.REQUIRE_MFA and "R-DUPLICATE" in d.rule_ids


def test_a_different_amount_an_older_payment_or_another_action_does_not_trigger_it() -> None:
    assert "R-DUPLICATE" not in evaluate(make_transfer(5_001), state((2, 5_000)), CFG, NOW).rule_ids
    assert (
        "R-DUPLICATE" not in evaluate(make_transfer(5_000), state((25, 5_000)), CFG, NOW).rule_ids
    )
    assert "R-DUPLICATE" not in evaluate(make_transfer(5_000), make_state(), CFG, NOW).rule_ids
    pay_inv = make_transfer(5_000, action=ActionType.PAY_INVOICE, invoice_id="inv1")
    assert "R-DUPLICATE" not in evaluate(pay_inv, state((2, 5_000)), CFG, NOW).rule_ids


def test_a_payee_with_no_account_key_or_an_unknown_payee_is_skipped_not_crashed() -> None:
    assert (
        "R-DUPLICATE"
        not in evaluate(make_transfer(5_000), state((2, 5_000), payee_key=""), CFG, NOW).rule_ids
    )
    assert (
        "R-DUPLICATE"
        not in evaluate(make_transfer(5_000, payee="ghost"), state((2, 5_000)), CFG, NOW).rule_ids
    )


def test_the_latest_matching_payment_is_the_one_named() -> None:
    d = evaluate(make_transfer(5_000), state((10, 5_000), (3, 5_000), (1, 7_000)), CFG, NOW)
    when = (NOW - timedelta(hours=3)).isoformat(timespec="minutes")
    assert any(r.startswith(f"R-DUPLICATE.same_payment@{when}") for r in d.reasons)


# ----------------------------------------------------------------------- after the hold (P2-7)
def aged(hours: float, *, prov, verified: bool) -> PayeeInfo:  # type: ignore[no-untyped-def]
    return PayeeInfo(
        currency=AUD, verified=verified, created_at=NOW - timedelta(hours=hours), provenance=prov,
        account_key="k1",
    )  # fmt: skip


def test_after_the_hold_an_unverified_untrusted_origin_payee_needs_mfa_with_the_warning_and_is_capped() -> (
    None
):
    from corridor.contracts import Provenance

    s = make_state(payees={"payee1": aged(25, prov=Provenance.UNTRUSTED, verified=False)})
    ok = evaluate(
        make_transfer(4_000), s, CFG, NOW
    )  # AUD 40.00 = NPR 4,173 at 104.34? rate in helper is 90
    assert ok.decision is D.REQUIRE_MFA and "R-PAYEE-COOLOFF" in ok.rule_ids
    assert any(r.startswith("R-PAYEE-COOLOFF.unverified_warning@") for r in ok.reasons)
    over = evaluate(make_transfer(5_556), s, CFG, NOW)  # AUD 55.56 at 90 is over NPR 5,000
    assert over.decision is D.DENY and any(
        r.startswith("R-PAYEE-COOLOFF.unverified_cap@") for r in over.reasons
    )


def test_the_cap_after_the_hold_counts_what_was_paid_in_the_last_24_hours() -> None:
    from corridor.contracts import Provenance

    items = (PaidItem(at=NOW - timedelta(hours=1), amount=Money(amount_minor=4_000, currency=AUD)),)
    s = make_state(
        payees={"payee1": aged(30, prov=Provenance.UNTRUSTED, verified=False)},
        paid_to_account={"k1": (items[0].amount,)}, paid_to_account_detail={"k1": items},
    )  # fmt: skip
    assert (
        evaluate(make_transfer(2_000), s, CFG, NOW).decision is D.DENY
    )  # 40 + 20 = 60 over the cap
    assert evaluate(make_transfer(1_000), s, CFG, NOW).decision is not D.DENY  # 50 is within it


def test_a_verified_or_user_typed_payee_is_unchanged_after_the_hold() -> None:
    from corridor.contracts import Provenance

    for prov, verified in ((Provenance.UNTRUSTED, True), (Provenance.USER_TYPED, False)):
        s = make_state(payees={"payee1": aged(25, prov=prov, verified=verified)})
        assert "R-PAYEE-COOLOFF" not in evaluate(make_transfer(500_000), s, CFG, NOW).rule_ids
