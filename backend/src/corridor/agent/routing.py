"""Route building for a transfer proposal.

P3-1: ``optimized_route`` picks the cheapest single channel that meets the deadline with the exact
optimizer (``corridor.optimizer.routes``, ADR 0013); ``build_route`` (the P1 fixed provider route,
kept for the baseline comparison and the cost replay) builds a one-leg ``RouteRecommendation`` for the configured default provider of a corridor, from the
numbers a READ tool produced (provider facts and the mid rate fact). The route is labelled
``default_unoptimized`` so nobody mistakes it for the cheapest route. Integer arithmetic only:
``fee_total = fixed + ceil(send * pct) + ceil((send - fixed - pct_fee) * margin)`` and
``delivered = floor((send - fee_total) * mid_rate)``.
"""

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from corridor.contracts.money import CurrencyCode, Money
from corridor.contracts.routing import RouteLeg, RouteRecommendation
from corridor.contracts.tools import FactKind, NumericFact, ToolResult
from corridor.optimizer.routes import Channel, cheapest_for_delivery, compare


class RoutingError(Exception):
    """No usable default route (missing data, amount outside provider limits)."""


@dataclass(frozen=True)
class ProviderTerms:
    """Fee structure of one provider in minor units of the source currency."""

    provider_id: str
    name: str
    fixed: int
    pct_bps: int
    margin_bps: int
    min_send: int
    max_send: int
    eta_hours: int


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def fees_and_delivered(send: int, t: ProviderTerms, mid: Decimal) -> tuple[int, int]:
    """(fee_total, delivered) for sending ``send`` minor units."""
    pct_fee = _ceil_div(send * t.pct_bps, 10_000)
    after = send - t.fixed - pct_fee
    margin_cost = _ceil_div(max(after, 0) * t.margin_bps, 10_000)
    fee_total = t.fixed + pct_fee + margin_cost
    net = send - fee_total
    delivered = int((Decimal(max(net, 0)) * mid).to_integral_value(rounding=ROUND_FLOOR))
    return fee_total, delivered


def provider_terms(providers: ToolResult, provider_id: str) -> ProviderTerms:
    """Read the numbers of ``provider_id`` out of a ``list_providers`` result (facts carry the ints)."""
    item = next(
        (p for p in providers.data.get("providers", []) if p.get("provider_id") == provider_id),
        None,
    )
    if item is None:
        raise RoutingError("default provider not available on this corridor")
    name = item["name"]
    facts = {f.label: f for f in providers.facts}

    def money(label: str) -> int:
        f = facts.get(f"{label} {name}")
        if f is None or f.amount_minor is None:
            raise RoutingError("provider terms missing")
        return f.amount_minor

    def count(label: str) -> int:
        f = facts.get(f"{label} {name}")
        if f is None or f.count is None:
            raise RoutingError("provider terms missing")
        return f.count

    return ProviderTerms(
        provider_id, name, money("fixed fee"), count("fee pct bps"), count("fx margin bps"),
        money("min send"), money("max send"), count("eta hours"),
    )  # fmt: skip


def mid_rate(quote: ToolResult) -> Decimal:
    """The mid rate fact of a ``get_fx_quote`` result."""
    for f in quote.facts:
        if f.kind is FactKind.RATE and f.rate is not None:
            return Decimal(f.rate)
    raise RoutingError("no mid rate available")


def build_route(
    terms: ProviderTerms,
    mid: Decimal,
    src: CurrencyCode,
    dst: CurrencyCode,
    *,
    send_minor: int | None = None,
    deliver_minor: int | None = None,
) -> RouteRecommendation:
    """A one leg route either for a given send amount or for a target delivered amount."""
    if (send_minor is None) == (deliver_minor is None):
        raise RoutingError("give exactly one of send_minor and deliver_minor")
    if send_minor is None:
        assert deliver_minor is not None
        lo, hi = (
            1,
            10**13,
        )  # delivered is non decreasing in send: smallest send that delivers enough
        if fees_and_delivered(hi, terms, mid)[1] < deliver_minor:
            raise RoutingError("amount too large")
        while lo < hi:
            mid_s = (lo + hi) // 2
            if fees_and_delivered(mid_s, terms, mid)[1] >= deliver_minor:
                hi = mid_s
            else:
                lo = mid_s + 1
        send_minor = lo
    if not terms.min_send <= send_minor <= terms.max_send:
        raise RoutingError("outside provider limits")
    fee, delivered = fees_and_delivered(send_minor, terms, mid)
    if delivered <= 0:
        raise RoutingError("amount too small after fees")
    rate = (Decimal(delivered) / Decimal(send_minor)).quantize(Decimal("1E-8"))
    leg = RouteLeg(
        provider_id=terms.provider_id,
        send_amount=Money(amount_minor=send_minor, currency=src),
        fee=Money(amount_minor=fee, currency=src),
        effective_rate=rate,
        delivered=Money(amount_minor=delivered, currency=dst),
        eta_hours=terms.eta_hours,
    )
    facts: tuple[NumericFact, ...] = (
        NumericFact(label="route send amount", kind=FactKind.MONEY, amount_minor=send_minor, currency=src),
        NumericFact(label="route total cost", kind=FactKind.MONEY, amount_minor=fee, currency=src),
        NumericFact(label="route delivered", kind=FactKind.MONEY, amount_minor=delivered, currency=dst),
        NumericFact(label="route eta hours", kind=FactKind.COUNT, count=terms.eta_hours),
    )  # fmt: skip
    return RouteRecommendation(
        legs=(leg,),
        total_cost=Money(amount_minor=fee, currency=src),
        effective_rate=rate,
        delivered=Money(amount_minor=delivered, currency=dst),
        eta_hours=terms.eta_hours,
        savings_vs_default=Money(amount_minor=0, currency=src),
        explanation_facts=facts,
    )


def route_summary(route: RouteRecommendation, provider_name: str) -> dict[str, Any]:
    """Display friendly summary (money formatted, no raw minor units in text)."""
    from corridor.formatting import format_money, format_rate

    leg = route.legs[0]
    return {
        "label": "cheapest_on_time",
        "provider": provider_name,
        "saving_vs_default": format_money(route.savings_vs_default),
        "send": format_money(leg.send_amount),
        "cost": format_money(route.total_cost),
        "delivered": format_money(route.delivered),
        "effective_rate": format_rate(route.effective_rate),
        "eta_hours": route.eta_hours,
    }


def channels_from(providers: ToolResult) -> list[Channel]:
    """Every active channel in a ``list_providers`` result (the facts carry the integers)."""
    out = []
    for p in providers.data.get("providers", []):
        name = p["name"]
        facts = {f.label: f for f in providers.facts}

        def num(label: str, name: str = name, facts: dict[str, NumericFact] = facts) -> int:
            f = facts.get(f"{label} {name}")
            v = None if f is None else (f.amount_minor if f.amount_minor is not None else f.count)
            if v is None:
                raise RoutingError("provider terms missing")
            return v

        out.append(
            Channel(
                p["provider_id"],
                name,
                num("fixed fee"),
                num("fee pct bps"),
                num("fx margin bps"),
                num("min send"),
                num("max send"),
                num("daily limit"),
                num("eta hours"),
            )
        )
    return out


def optimized_route(
    providers: ToolResult, mid: Decimal, src: CurrencyCode, dst: CurrencyCode, *,
    send_minor: int | None = None, deliver_minor: int | None = None,
    hours_until_deadline: int | None = None, default_provider_id: str | None = None,
) -> tuple[RouteRecommendation, str]:  # fmt: skip
    """The cheapest single channel route that meets the deadline, and its provider name.

    Exactly one of ``send_minor`` and ``deliver_minor``. ``savings_vs_default`` is the fee of the
    configured default provider minus this route's fee (never negative). Raises ``RoutingError`` when
    no channel can carry the amount, or none arrives before the deadline."""
    if (send_minor is None) == (deliver_minor is None):
        raise RoutingError("give exactly one of send_minor and deliver_minor")
    chans = channels_from(providers)
    if send_minor is not None:
        cmp = compare(chans, mid, send_minor, hours_until_deadline, max_legs=1)
        best = cmp.best
        if cmp.status.value == "deadline_unmet":
            raise RoutingError("no route arrives before the deadline")
    else:
        assert deliver_minor is not None
        best = cheapest_for_delivery(chans, mid, deliver_minor, hours_until_deadline)
    if best is None:
        raise RoutingError("amount outside every provider's limits")
    send = best.send
    default = next((c for c in chans if c.provider_id == default_provider_id), None)
    saved = 0
    if default is not None and default.min_send <= send <= default.cap:
        saved = max(default.cost(send, mid)[0] - best.fee, 0)
    leg = best.legs[0]
    rate = (Decimal(leg.delivered) / Decimal(leg.send)).quantize(Decimal("1E-8"))
    route_leg = RouteLeg(
        provider_id=leg.provider_id, send_amount=Money(amount_minor=leg.send, currency=src),
        fee=Money(amount_minor=leg.fee, currency=src), effective_rate=rate,
        delivered=Money(amount_minor=leg.delivered, currency=dst), eta_hours=leg.eta_hours,
    )  # fmt: skip
    facts = (
        NumericFact(label="route send amount", kind=FactKind.MONEY, amount_minor=send, currency=src),
        NumericFact(label="route total cost", kind=FactKind.MONEY, amount_minor=leg.fee, currency=src),
        NumericFact(label="route delivered", kind=FactKind.MONEY, amount_minor=leg.delivered, currency=dst),
        NumericFact(label="route eta hours", kind=FactKind.COUNT, count=leg.eta_hours),
        NumericFact(label="route saving versus default", kind=FactKind.MONEY, amount_minor=saved, currency=src),
    )  # fmt: skip
    rec = RouteRecommendation(
        legs=(route_leg,), total_cost=Money(amount_minor=leg.fee, currency=src), effective_rate=rate,
        delivered=Money(amount_minor=leg.delivered, currency=dst), eta_hours=leg.eta_hours,
        savings_vs_default=Money(amount_minor=saved, currency=src), explanation_facts=facts,
    )  # fmt: skip
    return rec, leg.name
