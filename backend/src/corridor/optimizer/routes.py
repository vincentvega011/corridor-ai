"""Bounded remittance route comparison (P3-1, as clarified by ADR 0013).

Every single-channel route and channel pair is enumerated. For each pair the full feasible split
interval is scanned when it contains at most 20,000 integer points; wider intervals scan only the
first and last 400 points. A wide-interval result is best among scanned candidates, not a proven
global optimum. Integer minor units only; the only ``Decimal`` is the mid rate.

Cost of sending ``x`` through a channel (same arithmetic as the proposal builder):
``fee = fixed + ceil(x * pct) + ceil((x - fixed - pct_fee) * margin)``, delivered
``floor((x - fee) * mid)``. A route meets a deadline if its slowest leg arrives in time. Routes that
break a per-transfer or daily limit are not feasible. This module only ranks routes; it never sends
anything (INV-1), and it imports no model client and makes no network call.
"""

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from enum import StrEnum

SCAN_ALL_UP_TO = 20_000  # a split range this narrow is scanned point by point
EDGE_WINDOW = 400  # wider ranges: the cost is near linear, so the best split is at a range end


def ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def fee_and_delivered(
    send: int, fixed: int, pct_bps: int, margin_bps: int, mid: Decimal
) -> tuple[int, int]:
    """(fee, delivered) for sending ``send`` minor units through one channel."""
    pct_fee = ceil_div(send * pct_bps, 10_000)
    after = send - fixed - pct_fee
    fee = fixed + pct_fee + ceil_div(max(after, 0) * margin_bps, 10_000)
    net = send - fee
    delivered = int((Decimal(max(net, 0)) * mid).to_integral_value(rounding=ROUND_FLOOR))
    return fee, delivered


@dataclass(frozen=True)
class Channel:
    """One remittance channel on a corridor (source currency minor units)."""

    provider_id: str
    name: str
    fixed: int
    pct_bps: int
    margin_bps: int
    min_send: int
    max_send: int
    daily_limit: int
    eta_hours: int

    @property
    def cap(self) -> int:
        """The most one route may send through this channel today."""
        return min(self.max_send, self.daily_limit)

    def cost(self, send: int, mid: Decimal) -> tuple[int, int]:
        return fee_and_delivered(send, self.fixed, self.pct_bps, self.margin_bps, mid)


@dataclass(frozen=True)
class Leg:
    provider_id: str
    name: str
    send: int
    fee: int
    delivered: int
    eta_hours: int


@dataclass(frozen=True)
class RouteOption:
    """One feasible route: its legs and what they add up to."""

    legs: tuple[Leg, ...]
    send: int
    fee: int
    delivered: int
    eta_hours: int
    meets_deadline: bool

    @property
    def label(self) -> str:
        return " + ".join(leg.name for leg in self.legs)

    def rank_key(self) -> tuple[int, int, int, int, str]:
        """Cheapest first; ties go to more delivered, fewer legs, faster, then provider ids."""
        return (
            self.fee, -self.delivered, len(self.legs), self.eta_hours,
            ",".join(leg.provider_id for leg in self.legs),
        )  # fmt: skip


class Status(StrEnum):
    OK = "ok"
    NO_ROUTE = "no_route"  # no channel can carry the amount at all
    DEADLINE_UNMET = "deadline_unmet"  # routes exist but none arrives in time


@dataclass(frozen=True)
class Comparison:
    """Everything the reply needs: ranked options, the best, and the saving."""

    status: Status
    send: int
    options: tuple[RouteOption, ...]  # every feasible route, cheapest first
    best: RouteOption | None  # cheapest route that meets the deadline
    worst_feasible: RouteOption | None  # most expensive route that meets the deadline
    savings_minor: int  # worst_feasible.fee - best.fee
    fastest: RouteOption | None  # fastest route overall (shown when the deadline cannot be met)


def _single(c: Channel, send: int, mid: Decimal, hours: int | None) -> RouteOption | None:
    if not c.min_send <= send <= c.cap:
        return None
    fee, delivered = c.cost(send, mid)
    if delivered <= 0:
        return None
    leg = Leg(c.provider_id, c.name, send, fee, delivered, c.eta_hours)
    return RouteOption(
        (leg,), send, fee, delivered, c.eta_hours, hours is None or c.eta_hours <= hours
    )


def _best_split(
    a: Channel, b: Channel, send: int, mid: Decimal, hours: int | None
) -> RouteOption | None:
    """Return the best scanned split for ``send`` through ``a`` and ``b`` together.

    The result is globally optimal only when the full feasible interval was scanned.
    """
    lo, hi = max(a.min_send, send - b.cap), min(a.cap, send - b.min_send)
    if lo > hi:
        return None
    if hi - lo + 1 <= SCAN_ALL_UP_TO:
        xs: range | list[int] = range(lo, hi + 1)
    else:
        xs = sorted({*range(lo, lo + EDGE_WINDOW), *range(hi - EDGE_WINDOW + 1, hi + 1)})
    best: RouteOption | None = None
    eta = max(a.eta_hours, b.eta_hours)
    for x in xs:
        fa, da = a.cost(x, mid)
        fb, db = b.cost(send - x, mid)
        if da <= 0 or db <= 0:
            continue
        legs = (
            Leg(a.provider_id, a.name, x, fa, da, a.eta_hours),
            Leg(b.provider_id, b.name, send - x, fb, db, b.eta_hours),
        )
        opt = RouteOption(legs, send, fa + fb, da + db, eta, hours is None or eta <= hours)
        if best is None or opt.rank_key() < best.rank_key():
            best = opt
    return best


def enumerate_routes(
    channels: list[Channel], mid: Decimal, send: int, hours: int | None = None, max_legs: int = 2
) -> list[RouteOption]:
    """Every feasible route: each single channel, and the best split of each pair."""
    chans = sorted(channels, key=lambda c: c.provider_id)
    out: list[RouteOption] = []
    for c in chans:
        if (o := _single(c, send, mid, hours)) is not None:
            out.append(o)
    if max_legs >= 2:
        for i, a in enumerate(chans):
            for b in chans[i + 1 :]:
                if (o := _best_split(a, b, send, mid, hours)) is not None:
                    out.append(o)
    return sorted(out, key=lambda o: o.rank_key())


def compare(
    channels: list[Channel], mid: Decimal, send: int, hours: int | None = None, max_legs: int = 2
) -> Comparison:
    """Rank all routes; pick the cheapest that meets the deadline (``hours`` from now, or None)."""
    if send <= 0:
        raise ValueError("send amount must be positive")
    options = enumerate_routes(channels, mid, send, hours, max_legs)
    if not options:
        return Comparison(Status.NO_ROUTE, send, (), None, None, 0, None)
    fastest = min(options, key=lambda o: (o.eta_hours, o.rank_key()))
    ok = [o for o in options if o.meets_deadline]
    if not ok:
        return Comparison(Status.DEADLINE_UNMET, send, tuple(options), None, None, 0, fastest)
    best, worst = ok[0], max(ok, key=lambda o: o.rank_key())
    return Comparison(Status.OK, send, tuple(options), best, worst, worst.fee - best.fee, fastest)


def cheapest_for_delivery(
    channels: list[Channel], mid: Decimal, deliver: int, hours: int | None = None
) -> RouteOption | None:
    """Single channel route delivering at least ``deliver`` for the lowest fee (no splits)."""
    best: RouteOption | None = None
    for c in sorted(channels, key=lambda c: c.provider_id):
        if hours is not None and c.eta_hours > hours:
            continue
        lo, hi = 1, c.cap
        if c.cost(hi, mid)[1] < deliver:
            continue
        while lo < hi:  # delivered never decreases as the send amount grows
            m = (lo + hi) // 2
            if c.cost(m, mid)[1] >= deliver:
                hi = m
            else:
                lo = m + 1
        if lo < c.min_send:
            lo = c.min_send
        opt = _single(c, lo, mid, hours)
        if (
            opt is not None
            and opt.delivered >= deliver
            and (best is None or opt.rank_key() < best.rank_key())
        ):
            best = opt
    return best
