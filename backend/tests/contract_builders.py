"""Shared builders for contract tests."""

from corridor.contracts import (
    ActionProposal,
    ActionType,
    CurrencyCode,
    Money,
    Provenance,
    RouteLeg,
    RouteRecommendation,
    Tainted,
)

HASH_A = "a" * 64


def make_money(minor: int = 1000, cur: CurrencyCode = CurrencyCode.AUD) -> Money:
    return Money(amount_minor=minor, currency=cur)


def make_route() -> RouteRecommendation:
    leg = RouteLeg(
        provider_id="swifthome",
        send_amount=make_money(100_000),
        fee=make_money(500),
        effective_rate="85.12345678",
        delivered=make_money(8_500_000, CurrencyCode.NPR),
        eta_hours=2,
    )
    return RouteRecommendation(
        legs=(leg,),
        total_cost=make_money(500),
        effective_rate="85.12345678",
        delivered=make_money(8_500_000, CurrencyCode.NPR),
        eta_hours=2,
        savings_vs_default=make_money(700),
    )


def make_proposal(**overrides: object) -> ActionProposal:
    base: dict[str, object] = {
        "proposal_id": "p1",
        "action": ActionType.TRANSFER,
        "user_id": "u1",
        "source_account_id": Tainted[str](value="acc1", provenance=Provenance.SYSTEM),
        "payee_id": Tainted[str](value="payee1", provenance=Provenance.SYSTEM),
        "amount": Tainted[Money](value=make_money(100_000), provenance=Provenance.USER_TYPED),
        "route": make_route(),
        "idempotency_key": "idem-1",
    }
    base.update(overrides)
    return ActionProposal(**base)  # type: ignore[arg-type]
