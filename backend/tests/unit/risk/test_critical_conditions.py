"""Exactly when a request is critical (score at or above 9000), from the configured weights (P3-1b, ADR 0016).

The claims below are the ones written in ADR 0016. The test enumerates real states through ``assess``,
so a change to a weight that breaks a claim fails here before the ADR becomes wrong."""

import itertools
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from corridor.contracts import Provenance
from corridor.contracts.risk import RiskLevel
from corridor.policy import AccountInfo, PayeeInfo
from corridor.risk.score import assess
from policy_helpers import AUD, CFG, NPR, make_state, make_transfer

NOW = datetime(2026, 10, 12, 2, 0, tzinfo=UTC)  # 13:00 Sydney
SMALL_HOURS = datetime(2026, 10, 11, 16, 0, tzinfo=UTC)  # 03:00 Sydney
HIST = 100_000  # median NPR 1,000.00
AMOUNTS = {
    "normal": 1_000,
    "unusual": 5_000,
    "extreme": 200_000,
}  # AUD minor; x90 against the median


def score(*, prov, verified, age, cop, docs, amount, hour, denials):  # type: ignore[no-untyped-def]
    payee = PayeeInfo(
        currency=AUD, verified=verified, created_at=NOW - age, provenance=prov, cop=cop
    )
    state = make_state(
        accounts={"acc1": AccountInfo(currency=AUD, balance_minor=10**9)},
        payees={"payee1": payee}, fx_to_npr={NPR: Decimal(1), AUD: Decimal("90")},
        history_median_npr_minor=HIST, timezone="Australia/Sydney", recent_denials=denials,
    )  # fmt: skip
    p = make_transfer(AMOUNTS[amount], derived_from_docs=("a" * 64,) if docs else ())
    return assess(p, state, CFG, hour)


GRID = list(
    itertools.product(
        (timedelta(hours=1), timedelta(days=2), timedelta(days=90)),  # age
        ("none", "match", "unknown"),  # cop, never a mismatch here
        (False, True),  # derived from a document
        ("normal", "unusual", "extreme"),
        (NOW, SMALL_HOURS),
        (0,),  # no recent denials
    )
)


def test_a_trusted_payee_without_denials_can_never_be_critical() -> None:
    """The user typed it (or the bank has it): new payee, big amount, small hours and a document together
    reach 8000 at most."""
    for prov in (Provenance.USER_TYPED, Provenance.SYSTEM):
        for verified in (False, True):
            for age, cop, docs, amount, hour, denials in GRID:
                a = score(prov=prov, verified=verified, age=age, cop=cop, docs=docs,
                          amount=amount, hour=hour, denials=denials)  # fmt: skip
                assert a.level is not RiskLevel.CRITICAL and a.score_bps <= 8000


def test_the_worst_case_for_a_typed_payee_is_exactly_eight_thousand() -> None:
    a = score(prov=Provenance.USER_TYPED, verified=False, age=timedelta(hours=1), cop="none",
              docs=True, amount="extreme", hour=SMALL_HOURS, denials=0)  # fmt: skip
    assert a.score_bps == 1500 + 3500 + 1500 + 1500 and a.level is RiskLevel.HIGH


def test_a_typed_payee_reaches_critical_only_with_three_recent_denials_and_the_worst_case() -> None:
    worst = dict(prov=Provenance.USER_TYPED, verified=False, age=timedelta(hours=1), cop="none",
                 docs=False, amount="extreme", hour=SMALL_HOURS)  # fmt: skip
    assert score(**worst, denials=2).level is not RiskLevel.CRITICAL  # 7500 or 8500
    assert score(**worst, denials=3).level is RiskLevel.CRITICAL  # three denials also lock the user


def test_a_name_mismatch_on_an_unverified_payee_is_critical_with_any_tainted_origin() -> None:
    a = score(prov=Provenance.UNTRUSTED, verified=False, age=timedelta(days=90), cop="mismatch",
              docs=False, amount="normal", hour=NOW, denials=0)  # fmt: skip
    assert a.score_bps == 9000 and a.level is RiskLevel.CRITICAL  # 6000 + 3000


def test_a_name_mismatch_alone_is_not_critical_and_a_verified_payee_cannot_mismatch() -> None:
    a = score(prov=Provenance.USER_TYPED, verified=False, age=timedelta(days=90), cop="mismatch",
              docs=False, amount="normal", hour=NOW, denials=0)  # fmt: skip
    assert a.score_bps == 6000 and a.level is RiskLevel.HIGH
    v = score(prov=Provenance.USER_TYPED, verified=True, age=timedelta(days=90), cop="mismatch",
              docs=False, amount="normal", hour=NOW, denials=0)  # fmt: skip
    assert (
        v.score_bps == 0
    )  # an officer verifies only on a match, so a verified payee is never penalised
