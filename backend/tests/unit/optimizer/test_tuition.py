"""Tuition funding plan: the CP-SAT answer must equal an exhaustive search exactly (P3-1)."""

import random
from decimal import Decimal

import pytest

from corridor.optimizer.routes import Channel
from corridor.optimizer.tuition import (
    FamilySource,
    OwnSource,
    PlanStatus,
    Problem,
    brute_force,
    mid_num,
    solve,
)


def tiny_channels(rng: random.Random) -> tuple[Channel, ...]:
    out = []
    for i in range(rng.randint(1, 2)):
        out.append(
            Channel(
                f"p{i}", f"P{i}", fixed=rng.choice([0, 1, 2]), pct_bps=rng.choice([0, 100, 250, 500]),
                margin_bps=rng.choice([0, 150, 400, 800]), min_send=rng.randint(1, 4),
                max_send=rng.randint(10, 16), daily_limit=rng.randint(12, 20), eta_hours=rng.choice([1, 24, 72]),
            )
    )  # fmt: skip
    return tuple(out)


def random_problem(seed: int) -> Problem:
    rng = random.Random(seed)
    own = [OwnSource("spend", rng.randint(2, 12), "spending", keep_minor=rng.randint(0, 2))]
    if rng.random() < 0.7:
        own.append(OwnSource("save", rng.randint(0, 6), "savings"))
    fam = ()
    if rng.random() < 0.9:
        mid = Decimal(rng.choice(["1", "1.25", "2", "2.7183"]))
        fam = (FamilySource("uncle", rng.randint(9, 16), mid, tiny_channels(rng)),)
    return Problem(rng.randint(12, 22), rng.choice([2, 24, 96]), tuple(own), fam)


@pytest.mark.parametrize("seed", range(16))
def test_solver_equals_exhaustive_search_on_small_instances(seed: int) -> None:
    p = random_problem(seed)
    plan = solve(p)
    expected = brute_force(p)
    if expected is None:
        assert plan.status is PlanStatus.INFEASIBLE
        assert plan.contributions == ()
    else:
        assert plan.status is PlanStatus.OK
        assert (
            plan.cost_minor,
            plan.family_received,
            plan.savings_used,
            plan.family_sources,
        ) == expected
        funded = sum(plan.pay_from_own.values()) + sum(
            c.amount_minor for c in plan.contributions if c.kind == "family"
        )
        assert funded >= p.amount_minor and sum(plan.pay_from_own.values()) <= p.amount_minor


def demo_problem(hours: int = 400, family_limit: int = 500_000) -> Problem:
    chans = (
        Channel(
            "prov_peakremit_qar_aud", "PeakRemit", 1200, 20, 60, 10_000, 2_000_000, 4_000_000, 24
        ),
        Channel("prov_swifthome_qar_aud", "SwiftHome", 0, 100, 120, 5_000, 500_000, 1_000_000, 2),
    )
    return Problem(
        650_000, hours,
        (OwnSource("acc_priya_aud", 420_000, "spending", keep_minor=50_000),
         OwnSource("acc_priya_aud_savings", 150_000, "savings")),
        (FamilySource("usr_ramesh", family_limit, Decimal("0.38"), chans),),
    )  # fmt: skip


def test_demo_case_uses_both_own_accounts_first_and_asks_family_for_the_rest() -> None:
    plan = solve(demo_problem())
    assert plan.status is PlanStatus.OK
    own = plan.pay_from_own
    assert own == {
        "acc_priya_aud": 370_000,
        "acc_priya_aud_savings": 150_000,
    }  # buffer of 500.00 kept
    fam = [c for c in plan.contributions if c.kind == "family"]
    assert len(fam) == 1 and fam[0].amount_minor >= 650_000 - 520_000


def test_no_family_needed_when_own_money_is_enough() -> None:
    p = demo_problem()
    p2 = Problem(p.amount_minor, p.hours_until_due,
                 (OwnSource("a", 900_000, "spending", keep_minor=100_000),), p.family)  # fmt: skip
    plan = solve(p2)
    assert plan.status is PlanStatus.OK and plan.family_sources == 0
    assert plan.pay_from_own == {"a": 650_000} and plan.cost_minor == 0


def test_infeasible_when_the_due_date_is_too_close_for_every_family_channel() -> None:
    plan = solve(demo_problem(hours=0))
    assert plan.status is PlanStatus.INFEASIBLE and plan.contributions == ()


def test_infeasible_when_the_family_limit_is_too_small() -> None:
    plan = solve(demo_problem(family_limit=100))
    assert plan.status is PlanStatus.INFEASIBLE


def test_savings_untouched_when_the_spending_account_is_enough() -> None:
    p = Problem(100, 24, (OwnSource("s", 150, "spending"), OwnSource("v", 500, "savings")))
    plan = solve(p)
    assert plan.pay_from_own == {"s": 100} and plan.savings_used == 0


def test_deterministic_and_never_a_partial_plan() -> None:
    p = demo_problem()
    assert solve(p) == solve(p)
    bad = Problem(0, 24, ())
    assert solve(bad).status is PlanStatus.NO_PLAN and solve(bad).contributions == ()


def test_the_linear_encoding_matches_the_route_fee_arithmetic() -> None:
    c = Channel("p", "P", 3, 150, 400, 1, 10_000, 10_000, 1)
    mid = Decimal("2.5")
    for send in (50, 777, 4_001):
        fee, delivered = c.cost(send, mid)
        p = Problem(delivered, 24, (), (FamilySource("f", send, mid, (c,)),))
        plan = solve(p)
        assert plan.status is PlanStatus.OK
        fam = next(x for x in plan.contributions if x.kind == "family")
        assert fam.amount_minor >= delivered and fam.send_minor <= send
        assert fam.fee_minor == c.cost(fam.send_minor, mid)[0]
    assert mid_num(mid) == 250_000_000
