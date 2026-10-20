"""Hypothesis properties of the policy engine (docs/spec/modules.md 5.7 acceptance, INV-2, INV-7)."""

from datetime import timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from corridor.contracts import ActionType, Money, Provenance
from corridor.contracts.policy import Decision
from corridor.policy import AccountInfo, PayeeInfo, evaluate
from corridor.policy.rules import LOCKABLE, OUTGOING, RuleResult, to_npr_minor
from policy_helpers import (
    AUD,
    CFG,
    NOW,
    NPR,
    SYSTEM,
    make_other,
    make_state,
    make_transfer,
    risk,
)  # fmt: skip

PROVS = st.sampled_from(list(Provenance))
TAINTED_PROVS = st.sampled_from([Provenance.UNTRUSTED, Provenance.MODEL_INFERRED])
BIG = 10**12


def state_for(
    *,
    payee_prov: Provenance = SYSTEM,
    verified: bool = True,
    age_days: int = 60,
    locked: bool = False,
    rate: Decimal = Decimal("90"),
    earlier: int = 0,
    times: tuple[int, ...] = (),
):  # type: ignore[no-untyped-def]
    from corridor.contracts import Money

    return make_state(
        accounts={"acc1": AccountInfo(currency=AUD, balance_minor=BIG)},
        payees={
            "payee1": PayeeInfo(
                currency=AUD,
                verified=verified,
                created_at=NOW - timedelta(days=age_days),
                provenance=payee_prov,
            )
        },
        fx_to_npr={NPR: Decimal(1), AUD: rate},
        locked=locked,
        outgoing_today=(Money(amount_minor=earlier, currency=AUD),) if earlier else (),
        recent_transfer_times=tuple(NOW - timedelta(seconds=t) for t in times),
    )


def proposal_for(action: ActionType, amount: int, **kw):  # type: ignore[no-untyped-def]
    if action in (ActionType.TRANSFER, ActionType.PAY_INVOICE, ActionType.CONVERT):
        return make_transfer(amount, **kw).model_copy(update={"action": action})
    return make_other(action)


amounts = st.integers(min_value=1, max_value=10**9)
rates = st.decimals(min_value=Decimal("0.5"), max_value=Decimal("200"), places=4)


@given(amount=amounts, rate=rates, amount_prov=PROVS, payee_prov=PROVS)
def test_a_amount_above_the_low_limit_is_never_allow_or_confirm_only(
    amount: int, rate: Decimal, amount_prov: Provenance, payee_prov: Provenance
) -> None:
    s = state_for(rate=rate)
    p = make_transfer(amount, amount_prov=amount_prov, payee_prov=payee_prov)
    npr = to_npr_minor(p.amount.value, s)  # type: ignore[union-attr]
    d = evaluate(p, s, CFG, NOW)
    if npr is not None and npr > CFG.limits.low_npr_minor:
        assert d.decision.rank >= Decision.REQUIRE_MFA.rank


@given(amount=amounts, payee_prov=TAINTED_PROVS, stored=st.booleans())
def test_b_an_untrusted_or_inferred_payee_never_yields_allow_or_confirm_alone(
    amount: int, payee_prov: Provenance, stored: bool
) -> None:
    if stored:  # taint stored in the database, proposal claims system
        d = evaluate(make_transfer(amount), state_for(payee_prov=payee_prov), CFG, NOW)
    else:  # taint carried by the proposal
        d = evaluate(make_transfer(amount, payee_prov=payee_prov), state_for(), CFG, NOW)
    assert d.decision.rank >= Decision.REQUIRE_MFA.rank


@given(
    amount=amounts,
    rate=rates,
    amount_prov=PROVS,
    payee_prov=PROVS,
    verified=st.booleans(),
    age=st.integers(0, 400),
    score=st.integers(0, 10_000),
    earlier=st.integers(0, 10**9),
    times=st.lists(st.integers(0, 900), max_size=10),
    locked=st.booleans(),
    verdict=st.sampled_from(list(Decision)),
    reason=st.text(min_size=1, max_size=5),
)
@settings(max_examples=300)
def test_c_adding_a_rule_never_makes_the_decision_less_restrictive(
    amount,
    rate,
    amount_prov,
    payee_prov,
    verified,
    age,
    score,
    earlier,
    times,
    locked,
    verdict,
    reason,
) -> None:  # type: ignore[no-untyped-def]
    s = state_for(
        payee_prov=payee_prov,
        verified=verified,
        age_days=age,
        locked=locked,
        rate=rate,
        earlier=earlier,
        times=tuple(times),
    )
    p = make_transfer(amount, amount_prov=amount_prov, risk=risk(score))
    base = evaluate(p, s, CFG, NOW)
    extra = lambda *_: [RuleResult("R-EXTRA", verdict, reason)]  # noqa: E731
    more = evaluate(p, s, CFG, NOW, extra_rules=(extra,))
    assert more.decision.rank >= base.decision.rank


@given(amount=amounts, action=st.sampled_from(sorted(LOCKABLE, key=lambda a: a.value)))
def test_d_a_locked_session_only_yields_deny_for_outgoing_actions(
    amount: int, action: ActionType
) -> None:
    s = state_for(locked=True)
    assert evaluate(proposal_for(action, amount), s, CFG, NOW).decision is Decision.DENY


@given(
    amount=amounts,
    rate=rates,
    amount_prov=PROVS,
    payee_prov=PROVS,
    verified=st.booleans(),
    age=st.integers(0, 400),
    score=st.integers(0, 10_000),
    locked=st.booleans(),
)
def test_e_decisions_are_deterministic(
    amount, rate, amount_prov, payee_prov, verified, age, score, locked
) -> None:  # type: ignore[no-untyped-def]
    s = state_for(payee_prov=payee_prov, verified=verified, age_days=age, locked=locked, rate=rate)
    p = make_transfer(amount, amount_prov=amount_prov, risk=risk(score))
    assert evaluate(p, s, CFG, NOW) == evaluate(p, s, CFG, NOW)


@given(
    amount=amounts,
    action=st.sampled_from(list(ActionType)),
    amount_prov=PROVS,
    payee_prov=PROVS,
    locked=st.booleans(),
)
def test_f_no_action_proposal_is_ever_allowed(
    amount: int, action: ActionType, amount_prov: Provenance, payee_prov: Provenance, locked: bool
) -> None:
    base = proposal_for(action, amount, amount_prov=amount_prov, payee_prov=payee_prov)
    d = evaluate(base, state_for(locked=locked), CFG, NOW)
    assert d.decision is not Decision.ALLOW
    if action in OUTGOING:
        assert d.decision.rank >= Decision.REQUIRE_CONFIRM.rank


# ---------------------------------------------------------------- R-PAYEE-COOLOFF (fooled-user control)
@given(
    amount=amounts,
    age_minutes=st.integers(min_value=0, max_value=24 * 60 - 1),
    amount_prov=PROVS,
    payee_prov=PROVS,
    risk_bps=st.sampled_from([None, 0, 6_000]),
    rate=rates,
    action=st.sampled_from([ActionType.TRANSFER, ActionType.PAY_INVOICE]),
)
def test_cooloff_a_young_account_never_gets_more_than_the_cap_whatever_the_provenance_or_risk(
    amount: int, age_minutes: int, amount_prov: Provenance, payee_prov: Provenance,
    risk_bps: int | None, rate: Decimal, action: ActionType,
) -> None:  # fmt: skip
    """A deceived user can confirm and pass MFA, so no grant level is enough: the decision itself is DENY."""
    s = state_for(rate=rate, age_days=0, payee_prov=payee_prov, verified=False)
    s = s.model_copy(
        update={
            "payees": {
                "payee1": s.payees["payee1"].model_copy(
                    update={"created_at": NOW - timedelta(minutes=age_minutes)}
                )
            }
        }
    )
    p = make_transfer(amount, amount_prov=amount_prov, payee_prov=payee_prov).model_copy(
        update={
            "action": action,
            "invoice_id": "inv1" if action is ActionType.PAY_INVOICE else None,
        }
    )
    if risk_bps is not None:
        p = p.model_copy(update={"risk": risk(risk_bps)})
    d = evaluate(p, s, CFG, NOW)
    npr = to_npr_minor(p.amount.value, s)  # type: ignore[union-attr]
    cap = (
        CFG.payee_cooloff.untrusted_origin_cap_npr_minor
        if payee_prov in (Provenance.UNTRUSTED, Provenance.MODEL_INFERRED)
        else CFG.payee_cooloff.first_payment_cap_npr_minor
    )
    if npr is None or npr > cap:
        assert d.decision is Decision.DENY and "R-PAYEE-COOLOFF" in d.rule_ids


@given(
    amount=st.integers(min_value=1, max_value=BIG),
    age_minutes=st.integers(min_value=0, max_value=24 * 60 - 1),
    payee_prov=TAINTED_PROVS,
    amount_prov=PROVS,
)
def test_cooloff_an_untrusted_origin_account_is_held_in_full_for_24_hours(
    amount: int, age_minutes: int, payee_prov: Provenance, amount_prov: Provenance
) -> None:
    """Part C: an account that came from a document, message or model guess can receive NOTHING for 24 hours."""
    assert CFG.payee_cooloff.untrusted_origin_cap_npr_minor == 0
    s = state_for(payee_prov=payee_prov, age_days=0)
    s = s.model_copy(
        update={
            "payees": {
                "payee1": s.payees["payee1"].model_copy(
                    update={"created_at": NOW - timedelta(minutes=age_minutes), "account_key": "k"}
                )
            }
        }
    )
    p = make_transfer(amount, amount_prov=amount_prov, payee_prov=payee_prov)
    d = evaluate(p, s, CFG, NOW)
    assert d.decision is Decision.DENY and "R-PAYEE-COOLOFF" in d.rule_ids
    assert any("first_payment_cap_untrusted@" in r and r.endswith("|0") for r in d.reasons)


@given(
    amount=st.integers(min_value=1, max_value=5_555),
    age_minutes=st.integers(min_value=0, max_value=24 * 60 - 1),
    payee_prov=st.sampled_from([Provenance.USER_TYPED, Provenance.USER_CONFIRMED, SYSTEM]),
)
def test_cooloff_an_account_the_user_typed_keeps_the_small_cap(
    amount: int, age_minutes: int, payee_prov: Provenance
) -> None:
    """A new payee the user typed themselves can still receive up to the cap (NPR 5,000 = AUD 55.55 at 90)."""
    s = state_for(payee_prov=payee_prov, age_days=0)
    s = s.model_copy(
        update={
            "payees": {
                "payee1": s.payees["payee1"].model_copy(
                    update={"created_at": NOW - timedelta(minutes=age_minutes), "account_key": "k"}
                )
            }
        }
    )
    d = evaluate(make_transfer(amount, payee_prov=payee_prov), s, CFG, NOW)
    assert "R-PAYEE-COOLOFF" not in d.rule_ids


@given(parts=st.lists(st.integers(min_value=1, max_value=20_000), min_size=1, max_size=8))
def test_cooloff_splitting_into_pieces_never_exceeds_the_cap_in_total(parts: list[int]) -> None:
    """Replay a sequence of payments to one young account: the running total of allowed ones stays at or under the cap."""
    from corridor.contracts import Money

    sent: list[Money] = []
    for amt in parts:
        s = state_for(age_days=0, verified=False).model_copy(
            update={"paid_to_account": {"k": tuple(sent)}}
        )
        s = s.model_copy(
            update={
                "payees": {
                    "payee1": s.payees["payee1"].model_copy(
                        update={"account_key": "k", "created_at": NOW - timedelta(hours=1)}
                    )
                }
            }
        )
        d = evaluate(make_transfer(amt), s, CFG, NOW)
        if d.decision is not Decision.DENY:
            sent.append(Money(amount_minor=amt, currency=AUD))
    total = sum(to_npr_minor(m, state_for()) or 0 for m in sent)
    assert total <= CFG.payee_cooloff.first_payment_cap_npr_minor


@given(
    extra=st.lists(st.integers(min_value=0, max_value=119), min_size=0, max_size=4), amount=amounts
)
def test_cooloff_re_adding_or_renaming_an_account_never_resets_its_age(
    extra: list[int], amount: int
) -> None:
    """Rows for the same account that are younger than the first one (a rename, a re-add) never loosen the decision."""
    base = state_for(age_days=0)
    first = base.payees["payee1"].model_copy(
        update={"account_key": "k", "created_at": NOW - timedelta(hours=2)}
    )
    s0 = base.model_copy(update={"payees": {"payee1": first}})
    rows = {"payee1": first} | {
        f"r{i}": first.model_copy(update={"created_at": NOW - timedelta(minutes=m)})
        for i, m in enumerate(extra)
    }  # every extra row is at most 119 minutes old, so younger than the first (120 minutes)
    s1 = base.model_copy(update={"payees": rows})
    d0, d1 = (
        evaluate(make_transfer(amount), s0, CFG, NOW),
        evaluate(make_transfer(amount), s1, CFG, NOW),
    )
    assert d1.decision.rank >= d0.decision.rank
    assert ("R-PAYEE-COOLOFF" in d0.rule_ids) == ("R-PAYEE-COOLOFF" in d1.rule_ids)


@given(
    amount=amounts,
    age_minutes=st.integers(min_value=0, max_value=24 * 60 - 1),
    untrusted=st.sampled_from([Provenance.UNTRUSTED, Provenance.MODEL_INFERRED]),
)
def test_cooloff_a_bank_verification_never_releases_an_untrusted_origin_account_early(
    amount: int, age_minutes: int, untrusted: Provenance
) -> None:
    """ADR 0018: only a payee the user typed is released by an officer's verification. An untrusted origin
    account stays held in full whatever an officer did, until the 24 hours are over (INV-4)."""
    s = state_for(age_days=0, payee_prov=untrusted, verified=True)
    s = s.model_copy(
        update={
            "payees": {
                "payee1": s.payees["payee1"].model_copy(
                    update={"created_at": NOW - timedelta(minutes=age_minutes), "account_key": "k"}
                )
            }
        }
    )
    d = evaluate(make_transfer(amount, payee_prov=untrusted), s, CFG, NOW)
    assert d.decision is Decision.DENY and "R-PAYEE-COOLOFF" in d.rule_ids


@given(amount=amounts, age_minutes=st.integers(min_value=0, max_value=24 * 60 - 1))
def test_cooloff_a_verified_typed_payee_is_not_held_but_an_unverified_one_is(
    amount: int, age_minutes: int
) -> None:
    for verified in (False, True):
        s = state_for(age_days=0, payee_prov=Provenance.USER_TYPED, verified=verified)
        s = s.model_copy(
            update={
                "payees": {
                    "payee1": s.payees["payee1"].model_copy(
                        update={
                            "created_at": NOW - timedelta(minutes=age_minutes),
                            "account_key": "k",
                        }
                    )
                }
            }
        )
        d = evaluate(make_transfer(amount, payee_prov=Provenance.USER_TYPED), s, CFG, NOW)
        if verified:
            assert "R-PAYEE-COOLOFF" not in d.rule_ids
        elif to_npr_minor(Money(amount_minor=amount, currency=AUD), state_for()) > (
            CFG.payee_cooloff.first_payment_cap_npr_minor
        ):
            assert d.decision is Decision.DENY and "R-PAYEE-COOLOFF" in d.rule_ids


def test_cooloff_a_verified_typed_payee_without_an_account_key_is_still_held() -> None:
    """The fail closed branch of the ADR 0018 rule: no account key means no release."""
    s = state_for(age_days=0, payee_prov=Provenance.USER_TYPED, verified=True)
    s = s.model_copy(
        update={
            "payees": {
                "payee1": s.payees["payee1"].model_copy(
                    update={"created_at": NOW - timedelta(hours=1), "account_key": ""}
                )
            }
        }
    )
    d = evaluate(make_transfer(500_000, payee_prov=Provenance.USER_TYPED), s, CFG, NOW)
    assert d.decision is Decision.DENY and "R-PAYEE-COOLOFF" in d.rule_ids
