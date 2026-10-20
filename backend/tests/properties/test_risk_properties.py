"""The risk rule can only make a decision stricter, never looser (INV-7, P3-1)."""

from datetime import timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

import corridor.policy.engine as engine
from corridor.contracts import Provenance
from corridor.policy import AccountInfo, PayeeInfo, evaluate
from corridor.policy.rules import RULES, r_risk
from corridor.risk.score import assess
from policy_helpers import AUD, CFG, NOW, NPR, make_state, make_transfer, risk


def build(prov, verified, age_days, cop, denials, median, tz):  # type: ignore[no-untyped-def]
    return make_state(
        accounts={"acc1": AccountInfo(currency=AUD, balance_minor=10**12)},
        payees={
            "payee1": PayeeInfo(
                currency=AUD, verified=verified, created_at=NOW - timedelta(days=age_days),
                provenance=prov, cop=cop,
            )
        },
        fx_to_npr={NPR: Decimal(1), AUD: Decimal("90")},
        recent_denials=denials, history_median_npr_minor=median, timezone=tz,
    )  # fmt: skip


STATES = st.builds(
    build,
    st.sampled_from(list(Provenance)),
    st.booleans(),
    st.integers(0, 400),
    st.sampled_from(["match", "mismatch", "unknown"]),
    st.integers(0, 6),
    st.sampled_from([0, 1_000, 100_000, 10_000_000]),
    st.sampled_from(["UTC", "Asia/Kathmandu", "Australia/Sydney", "Nope/Nope"]),
)


@settings(max_examples=150, deadline=None)
@given(
    state=STATES,
    amount=st.integers(1, 10**8),
    carried=st.integers(0, 10000),
    docs=st.booleans(),
)
def test_adding_the_risk_rule_never_loosens_a_decision(state, amount, carried, docs) -> None:  # type: ignore[no-untyped-def]
    p = make_transfer(amount, derived_from_docs=("a" * 64,) if docs else (), risk=risk(carried))
    with_risk = evaluate(p, state, CFG, NOW)
    original = engine.RULES
    try:
        engine.RULES = tuple(r for r in RULES if r is not r_risk)
        without = evaluate(p, state, CFG, NOW)
    finally:
        engine.RULES = original
    assert with_risk.decision.rank >= without.decision.rank


@settings(max_examples=100, deadline=None)
@given(state=STATES, amount=st.integers(1, 10**8))
def test_score_is_bounded_deterministic_and_monotone_in_denials(state, amount) -> None:  # type: ignore[no-untyped-def]
    p = make_transfer(amount)
    a = assess(p, state, CFG, NOW)
    assert 0 <= a.score_bps <= 10000 and a == assess(p, state, CFG, NOW)
    more = state.model_copy(update={"recent_denials": state.recent_denials + 1})
    assert assess(p, more, CFG, NOW).score_bps >= a.score_bps
