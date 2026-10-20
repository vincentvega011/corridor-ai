"""Read-only tools exposed to the planner (docs/spec/modules.md 5.1).

Security design (INV-1, INV-12, INV-3):
- Signature ``tool(ctx, args) -> ToolResult``. ``ToolContext.user_id`` comes from the authenticated
  session. No args model has a user field and all args models forbid extra keys, so a model can
  never name another user. Every user data query is filtered by ``ctx.user_id``; an id that
  belongs to someone else behaves exactly like an id that does not exist (``found: false``).
- The session is a Postgres READ ONLY transaction (``db.read_only_session``), so no tool can write
  even by mistake. There is no write tool anywhere: ``ToolKind`` has only READ.
- ``ToolResult.data`` is what may reach a prompt: money is rendered as ``NPR 1,000,000.00``
  (``corridor.formatting``), account numbers appear only as ``****1234``, hashes are shortened.
  The exact integers and rates go to ``ToolResult.facts`` for the grounding registry (INV-9).
- Values stored in the bank database are SYSTEM data, including an invoice's stored beneficiary,
  which is the trusted record that policy compares untrusted documents against.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from corridor.contracts.money import CurrencyCode, MinorUnits
from corridor.contracts.scalars import IdStr
from corridor.contracts.tools import FactKind, NumericFact, ToolKind, ToolResult, ToolSpec
from corridor.formatting import format_bps, format_date, format_minor, format_rate
from corridor.optimizer.routes import Channel, RouteOption, compare
from corridor.optimizer.tuition import (
    FamilySource,
    OwnSource,
    PlanStatus,
    Problem,
    load_spending_buffers,
)
from corridor.optimizer.tuition import solve as solve_funding
from corridor.sandbox.models import (
    Account,
    CreditEvent,
    FamilyLink,
    FxMidRate,
    Invoice,
    Passport,
    Payee,
    Provider,
    Schedule,
    Transfer,
    User,
)


def _timezone_of(region: str | None) -> str:
    """The IANA zone of a region of residence (``config/corridors.yaml``), UTC when unknown."""
    import yaml

    path = Path(__file__).resolve().parents[3] / "config" / "corridors.yaml"
    table = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("region_timezone") or {}
    return str(table.get(region or "", "UTC"))


class ToolError(Exception):
    """A tool call was rejected (bad arguments or unknown tool). Fail closed (INV-10)."""


@dataclass(frozen=True)
class ToolContext:
    """Trusted per-call context. ``user_id`` is never taken from model output."""

    user_id: str
    session: Session
    today: date
    call_id: str
    now: datetime | None = None
    """The sandbox clock (demo clock in demo mode); arrival times are this plus each route's speed."""
    raw_payee_names: bool = False
    """Only the evaluation baseline (a naive agent with no INV-3 defence) sets this: it sees raw names."""


class _Args(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class NoArgs(_Args):
    """Tool without arguments."""


class GetPayeesArgs(_Args):
    region: str | None = Field(default=None, max_length=8)


def _bounded_amount(v: int | None) -> int | None:
    """Enforced here, not in the JSON schema: a large numeric bound in a schema that is sent to a
    model would trip the prompt guard (9 or more digits)."""
    if v is not None and not 0 < v <= 10**13:
        raise ValueError("amount_minor out of range")
    return v


class GetFxQuoteArgs(_Args):
    from_currency: CurrencyCode
    to_currency: CurrencyCode
    amount_minor: Annotated[MinorUnits | None, AfterValidator(_bounded_amount)] = None


class ListProvidersArgs(_Args):
    from_currency: CurrencyCode
    to_currency: CurrencyCode


class CompareRoutesArgs(_Args):
    from_currency: CurrencyCode
    to_currency: CurrencyCode
    amount_minor: Annotated[MinorUnits, AfterValidator(_bounded_amount)]
    deadline: date | None = None


class PlanTuitionArgs(_Args):
    invoice_id: IdStr


class ListPassportsArgs(_Args):
    pass


class GetInvoiceArgs(_Args):
    invoice_id: IdStr


class GetHistoryArgs(_Args):
    days: int = Field(default=90, ge=1, le=366)
    limit: int = Field(default=50, ge=1, le=100)
    kind: Literal["remittance", "domestic"] | None = None


def _money_fact(label: str, minor: int, ccy: str) -> NumericFact:
    return NumericFact(
        label=label, kind=FactKind.MONEY, amount_minor=minor, currency=CurrencyCode(ccy)
    )


def _count_fact(label: str, n: int) -> NumericFact:
    return NumericFact(label=label, kind=FactKind.COUNT, count=n)


def _date_fact(label: str, d: date) -> NumericFact:
    return NumericFact(label=label, kind=FactKind.DATE, on=d)


def _result(
    ctx: ToolContext, tool: str, data: dict[str, Any], facts: list[NumericFact]
) -> ToolResult:
    return ToolResult(tool_call_id=ctx.call_id, tool=tool, data=data, facts=tuple(facts))


def get_balances(ctx: ToolContext, args: NoArgs) -> ToolResult:
    """Balances of the user's own accounts."""
    rows = ctx.session.scalars(
        select(Account).where(Account.user_id == ctx.user_id).order_by(Account.id)
    ).all()
    facts = [_count_fact("account_count", len(rows))]
    items = []
    for a in rows:
        items.append(
            {
                "account_id": a.id,
                "currency": a.currency,
                "balance": format_minor(a.balance_minor, CurrencyCode(a.currency)),
                "account_no": a.account_no_masked,
                "status": a.status,
                "kind": a.kind,
                "label": f"{a.currency} {a.kind} account {a.account_no_masked}",
            }
        )
        facts.append(_money_fact(f"balance {a.id}", a.balance_minor, a.currency))
    return _result(ctx, "get_balances", {"accounts": items}, facts)


from corridor.sandbox.doc_payee import document_label  # noqa: E402


def get_payees(ctx: ToolContext, args: GetPayeesArgs) -> ToolResult:
    """The user's own saved payees (masked account numbers only)."""
    stmt = select(Payee).where(Payee.user_id == ctx.user_id).order_by(Payee.id)
    if args.region is not None:
        stmt = stmt.where(Payee.region == args.region.upper())
    rows = ctx.session.scalars(stmt).all()
    items = [
        {
            "payee_id": p.id,
            # a payee made from a document, message or model guess shows a fixed label, never its raw name
            # (INV-3, docs/spec/modules.md 5.1); deterministic code reads the sanitised name from the database
            "display_name": document_label(p.account_no_masked)
            if not ctx.raw_payee_names and (p.provenance != "system" or p.added_via == "document")
            else p.display_name,
            "name_untrusted": p.provenance != "system" or p.added_via == "document",
            "region": p.region,
            "currency": p.currency,
            "account_no": p.account_no_masked,
            "verified": p.verified_at is not None,
            "added_via": p.added_via,
            "provider_ref": p.provider_ref,
            "relation": p.relation,
            "provenance": p.provenance,
        }
        for p in rows
    ]
    return _result(ctx, "get_payees", {"payees": items}, [_count_fact("payee_count", len(rows))])


def _latest_rate(session: Session, pair: str) -> FxMidRate | None:
    return session.scalars(
        select(FxMidRate).where(FxMidRate.pair == pair).order_by(FxMidRate.as_of.desc()).limit(1)
    ).first()


def _rate_between(session: Session, a: str, b: str) -> tuple[Decimal, date, str] | None:
    """Direct, inverse, or cross rate through NPR (every NRB quote is against NPR)."""
    direct = _latest_rate(session, f"{a}/{b}")
    if direct is not None:
        return direct.rate, direct.as_of, "direct"
    inverse = _latest_rate(session, f"{b}/{a}")
    if inverse is not None:
        return Decimal(1) / inverse.rate, inverse.as_of, "inverse"
    if "NPR" not in (a, b):
        first, second = _rate_between(session, a, "NPR"), _rate_between(session, "NPR", b)
        if first and second:
            return first[0] * second[0], min(first[1], second[1]), "cross via NPR"
    return None


def get_fx_quote(ctx: ToolContext, args: GetFxQuoteArgs) -> ToolResult:
    """Indicative mid rate (no provider margin) and, optionally, the converted amount."""
    a, b = args.from_currency.value, args.to_currency.value
    found = None if a == b else _rate_between(ctx.session, a, b)
    if found is None:
        return _result(ctx, "get_fx_quote", {"found": False, "pair": f"{a}/{b}"}, [])
    rate, as_of, basis = found
    q = rate.quantize(Decimal("1E-8"), rounding=ROUND_HALF_EVEN)
    facts = [
        NumericFact(label=f"mid rate {a}/{b}", kind=FactKind.RATE, rate=q),
        _date_fact("rate date", as_of),
    ]
    data: dict[str, Any] = {
        "found": True,
        "pair": f"{a}/{b}",
        "mid_rate": format_rate(q),
        "as_of": format_date(as_of),
        "basis": basis,
        "source": "Nepal Rastra Bank buy/sell mid (illustrative sandbox data)",
    }
    if args.amount_minor is not None:
        converted = int((Decimal(args.amount_minor) * q).to_integral_value(ROUND_HALF_EVEN))
        data["amount"] = format_minor(args.amount_minor, args.from_currency)
        data["converted_at_mid"] = format_minor(converted, args.to_currency)
        facts += [
            _money_fact("amount to convert", args.amount_minor, a),
            _money_fact("converted at mid", converted, b),
        ]
    return _result(ctx, "get_fx_quote", data, facts)


def list_providers(ctx: ToolContext, args: ListProvidersArgs) -> ToolResult:
    """Active providers on a corridor with their fee structures (reference data, not user data)."""
    a, b = args.from_currency.value, args.to_currency.value
    rows = ctx.session.scalars(
        select(Provider)
        .where(Provider.corridor_from == a, Provider.corridor_to == b, Provider.active.is_(True))
        .order_by(Provider.id)
    ).all()
    facts = [_count_fact("provider_count", len(rows))]
    items = []
    for p in rows:
        items.append(
            {
                "provider_id": p.id,
                "name": p.name,
                "fixed_fee": format_minor(p.fee_fixed_minor, args.from_currency),
                "fee_pct": format_bps(p.fee_pct_bps),
                "fx_margin": format_bps(p.fx_margin_bps),
                "min_send": format_minor(p.min_minor, args.from_currency),
                "max_send": format_minor(p.max_minor, args.from_currency),
                "daily_limit": format_minor(p.daily_limit_minor, args.from_currency),
                "eta_hours": p.eta_hours,
            }
        )
        facts += [
            _money_fact(f"fixed fee {p.name}", p.fee_fixed_minor, a),
            _count_fact(f"fee pct bps {p.name}", p.fee_pct_bps),
            _count_fact(f"fx margin bps {p.name}", p.fx_margin_bps),
            _money_fact(f"min send {p.name}", p.min_minor, a),
            _money_fact(f"max send {p.name}", p.max_minor, a),
            _money_fact(f"daily limit {p.name}", p.daily_limit_minor, a),
            _count_fact(f"eta hours {p.name}", p.eta_hours),
        ]
    return _result(ctx, "list_providers", {"corridor": f"{a}/{b}", "providers": items}, facts)


def compare_routes(ctx: ToolContext, args: CompareRoutesArgs) -> ToolResult:
    """Every feasible route for an amount, cheapest first (exact enumeration, ADR 0013).

    Reference data only. The deadline is counted conservatively: a route is on time if its ETA fits in
    whole days until the deadline day starts. Arrival dates assume the money is sent today."""
    a, b = args.from_currency.value, args.to_currency.value
    found = None if a == b else _rate_between(ctx.session, a, b)
    rows = ctx.session.scalars(
        select(Provider)
        .where(Provider.corridor_from == a, Provider.corridor_to == b, Provider.active.is_(True))
        .order_by(Provider.id)
    ).all()
    if found is None or not rows:
        return _result(ctx, "compare_routes", {"found": False, "pair": f"{a}/{b}"}, [])
    mid = found[0].quantize(Decimal("1E-8"), rounding=ROUND_HALF_EVEN)
    chans = [
        Channel(p.id, p.name, p.fee_fixed_minor, p.fee_pct_bps, p.fx_margin_bps, p.min_minor,
                p.max_minor, p.daily_limit_minor, p.eta_hours)
        for p in rows
    ]  # fmt: skip
    hours = None if args.deadline is None else max((args.deadline - ctx.today).days, 0) * 24
    cmp = compare(chans, mid, args.amount_minor, hours, max_legs=1)  # one transfer each
    split = compare(chans, mid, args.amount_minor, hours, max_legs=2).best
    src, dst = args.from_currency, args.to_currency
    start = ctx.now or datetime.combine(ctx.today, time(0), tzinfo=UTC)
    owner = ctx.session.get(User, ctx.user_id)
    zone = ZoneInfo(_timezone_of(owner.home_region if owner is not None else None))
    facts: list[NumericFact] = [
        _money_fact("amount to send", args.amount_minor, a),
        _count_fact("route_count", len(cmp.options)),
    ]
    if args.deadline is not None:
        facts += [
            _date_fact("deadline", args.deadline),
            _count_fact("hours until deadline", hours or 0),
        ]

    def show(o: RouteOption, tag: str) -> dict[str, Any]:
        arrives_at = start + timedelta(hours=o.eta_hours)  # a 2 hour route arrives the same day
        arrives = arrives_at.astimezone(zone).date()  # the date in the customer's own time zone
        facts.extend(
            [
                _money_fact(f"{tag} total cost", o.fee, a),
                _money_fact(f"{tag} received", o.delivered, b),
                _count_fact(f"{tag} eta hours", o.eta_hours),
                _date_fact(f"{tag} arrives by", arrives),
            ]
        )
        legs = []
        for i, leg in enumerate(o.legs):
            facts.append(_money_fact(f"{tag} leg {i + 1} send", leg.send, a))
            legs.append({"provider": leg.name, "send": format_minor(leg.send, src)})
        return {
            "route": o.label, "legs": legs, "total_cost": format_minor(o.fee, src),
            "received": format_minor(o.delivered, dst), "eta_hours": o.eta_hours,
            "arrives_by": format_date(arrives), "arrives_at": arrives_at.astimezone(UTC).isoformat(),
            "meets_deadline": o.meets_deadline,
        }  # fmt: skip

    data: dict[str, Any] = {
        "found": True, "pair": f"{a}/{b}", "status": cmp.status.value,
        "amount": format_minor(args.amount_minor, src), "mid_rate": format_rate(mid),
        "mid_rate_precise": format(mid.quantize(Decimal("0.0001")), "f").rstrip("0").rstrip("."),
        "rate_date": format_date(found[1]),
        "rate_source": "NRB reference rate (Nepal Rastra Bank), illustrative sandbox data",
        "note": "illustrative sandbox providers; the first alternative is the cheapest",
    }  # fmt: skip
    if args.deadline is not None:
        data["deadline"] = format_date(args.deadline)
    if cmp.best is not None and cmp.worst_feasible is not None:
        data["recommended"] = show(cmp.best, "recommended")
        data["alternatives"] = [
            show(o, f"option {i + 1}") for i, o in enumerate(cmp.options) if o is not cmp.best
        ][:5]
        data["saving_vs_most_expensive"] = format_minor(cmp.savings_minor, src)
        facts.append(_money_fact("saving versus most expensive route", cmp.savings_minor, a))
        if split is not None and len(split.legs) == 2 and split.fee < cmp.best.fee:
            data["split_tip"] = show(split, "split") | {
                "note": "two separate transfers, each needs its own confirmation",
                "extra_saving": format_minor(cmp.best.fee - split.fee, src),
            }
            facts.append(_money_fact("extra saving by splitting", cmp.best.fee - split.fee, a))
    elif cmp.fastest is not None:
        data["fastest_available"] = show(cmp.fastest, "fastest")
    return _result(ctx, "compare_routes", data, facts)


def _family_label(session: Session, user_id: str, contributor_id: str) -> dict[str, Any]:
    """Who a family contributor is, in words: first name, relation to the requester, region."""
    link = session.scalars(
        select(FamilyLink).where(
            FamilyLink.user_id == user_id, FamilyLink.contributor_user_id == contributor_id
        )
    ).first()
    who = session.get(User, contributor_id)
    name = (who.display_name.split("(")[0].split()[0]) if who is not None else "?"
    return {
        "from_name": name,
        "from_relation": link.relation if link is not None else None,
        "from_region": who.home_region if who is not None else None,
    }


def plan_tuition_funding(ctx: ToolContext, args: PlanTuitionArgs) -> ToolResult:
    """The cheapest way to fund what is still owed on one of the user's invoices (CP-SAT, ADR 0014).

    Reference computation only: it reads the user's own accounts and the family links set up by the bank
    (never another user's balance) and returns a plan as numbers. Paying and asking are separate
    proposals the user confirms one by one. A due date counts conservatively (whole days from today)."""
    inv = ctx.session.scalars(
        select(Invoice).where(
            Invoice.id == args.invoice_id,
            Invoice.user_id == ctx.user_id,
            Invoice.status.in_(("open", "part_paid")),
        )
    ).first()
    if inv is None:
        return _result(ctx, "plan_tuition_funding", {"found": False}, [])
    ccy = CurrencyCode(inv.currency)
    owed = inv.amount_minor - inv.paid_minor
    buffers = load_spending_buffers()
    accounts = ctx.session.scalars(
        select(Account).where(
            Account.user_id == ctx.user_id, Account.currency == inv.currency, Account.status == "active"
        ).order_by(Account.id)
    ).all()  # fmt: skip
    own = tuple(
        OwnSource(
            a.id,
            a.balance_minor,
            a.kind,
            buffers.get(inv.currency, 0) if a.kind == "spending" else 0,
        )
        for a in accounts
    )
    family: list[FamilySource] = []
    rate_dates: list[date] = []
    for fl in ctx.session.scalars(select(FamilyLink).where(FamilyLink.user_id == ctx.user_id)):
        acct = ctx.session.get(Account, fl.contributor_account_id)
        if acct is None:
            continue
        rate = _rate_between(ctx.session, acct.currency, inv.currency)
        rows = ctx.session.scalars(
            select(Provider).where(
                Provider.corridor_from == acct.currency,
                Provider.corridor_to == inv.currency,
                Provider.active.is_(True),
            ).order_by(Provider.id)
        ).all()  # fmt: skip
        if rate is None or not rows:
            continue
        chans = tuple(
            Channel(p.id, p.name, p.fee_fixed_minor, p.fee_pct_bps, p.fx_margin_bps, p.min_minor,
                    p.max_minor, p.daily_limit_minor, p.eta_hours)
            for p in rows
        )  # fmt: skip
        rate_dates.append(rate[1])
        family.append(
            FamilySource(
                fl.contributor_user_id,
                fl.per_request_limit_minor,
                rate[0].quantize(Decimal("1E-8"), rounding=ROUND_HALF_EVEN),
                chans,
            )
        )
    hours = max((inv.due_date - ctx.today).days, 0) * 24
    plan = solve_funding(Problem(owed, hours, own, tuple(family)))
    facts: list[NumericFact] = [
        _money_fact("invoice amount", inv.amount_minor, inv.currency),
        _money_fact("still owed", owed, inv.currency),
        _date_fact("due date", inv.due_date),
    ]
    data: dict[str, Any] = {
        "found": True, "invoice_id": inv.id, "status": plan.status.value, "currency": inv.currency,
        "invoice_label": f"{inv.issuer}, {inv.description}" if inv.description else inv.issuer,
        "amount": format_minor(inv.amount_minor, ccy), "still_owed": format_minor(owed, ccy),
        "due_date": format_date(inv.due_date), "steps": [],
        "rate_date": format_date(min(rate_dates)) if rate_dates else None,
        "rate_source": "NRB reference rate (Nepal Rastra Bank), illustrative sandbox data",
    }  # fmt: skip
    if plan.status is not PlanStatus.OK:
        return _result(ctx, "plan_tuition_funding", data, facts)
    by_fam = {f.source_id: f for f in family}
    steps: list[dict[str, Any]] = []
    for c in sorted(
        (c for c in plan.contributions if c.kind == "family"), key=lambda c: c.source_id
    ):
        link_acct = next(
            fl.contributor_account_id
            for fl in ctx.session.scalars(
                select(FamilyLink).where(FamilyLink.user_id == ctx.user_id)
            )
            if fl.contributor_user_id == c.source_id
        )
        acct = ctx.session.get(Account, link_acct)
        assert acct is not None
        n = len(steps) + 1
        facts += [
            _money_fact(f"step {n} request", c.send_minor, acct.currency),
            _money_fact(f"step {n} arrives", c.amount_minor, inv.currency),
            _money_fact(f"step {n} fee", c.fee_minor, acct.currency),
            _count_fact(f"step {n} eta hours", c.eta_hours),
        ]
        steps.append(
            {
                "step": n,
                "type": "request_contribution",
                "from_user": c.source_id,
                **_family_label(ctx.session, ctx.user_id, c.source_id),
                "target_account": next(a.id for a in accounts if a.kind == "spending"),
                "target_account_label": next(
                    f"{a.currency} {a.kind} account {a.account_no_masked}"
                    for a in accounts
                    if a.kind == "spending"
                ),
                "request": format_minor(c.send_minor, CurrencyCode(acct.currency)),
                "request_minor": c.send_minor,
                "request_currency": acct.currency,
                "arrives": format_minor(c.amount_minor, ccy),
                "arrives_minor": c.amount_minor,
                "route": next(
                    ch.name
                    for ch in by_fam[c.source_id].channels
                    if ch.provider_id == c.provider_id
                ),
                "fee": format_minor(c.fee_minor, CurrencyCode(acct.currency)),
                "eta_hours": c.eta_hours,
            }
        )
    for c in sorted(
        (c for c in plan.contributions if c.kind != "family"),
        key=lambda c: (c.kind != "spending", c.source_id),
    ):
        n = len(steps) + 1
        facts.append(_money_fact(f"step {n} payment", c.amount_minor, inv.currency))
        steps.append(
            {
                "step": n,
                "type": "pay_invoice",
                "from_account": c.source_id,
                "from_account_label": next(
                    (
                        f"{a.currency} {a.kind} account {a.account_no_masked}"
                        for a in accounts
                        if a.id == c.source_id
                    ),
                    c.source_id,
                ),
                "kind": c.kind,
                "amount": format_minor(c.amount_minor, ccy),
                "amount_minor": c.amount_minor,
            }
        )
    asked = sum(c.amount_minor for c in plan.contributions if c.kind == "family")
    data["steps"] = steps
    data["total_fees"] = format_minor(plan.cost_minor, ccy)
    data["paid_now"] = format_minor(sum(plan.pay_from_own.values()), ccy)
    data["after_family_arrives"] = format_minor(max(owed - sum(plan.pay_from_own.values()), 0), ccy)
    facts += [
        _money_fact("total fees", plan.cost_minor, inv.currency),
        _money_fact("paid now from own accounts", sum(plan.pay_from_own.values()), inv.currency),
        _money_fact("still to pay after family arrives", max(owed - sum(plan.pay_from_own.values()), 0), inv.currency),
        _money_fact("family money arriving", asked, inv.currency),
    ]  # fmt: skip
    return _result(ctx, "plan_tuition_funding", data, facts)


def list_passports(ctx: ToolContext, args: ListPassportsArgs) -> ToolResult:
    """The user's issued Credit Passports: ids, which claims each holds and when it expires.

    Claim values and salts are never returned here (they stay with the holder); this is only what the
    agent needs to offer a share."""
    rows = ctx.session.scalars(
        select(Passport).where(Passport.user_id == ctx.user_id).order_by(Passport.issued_at.desc())
    ).all()
    facts = [_count_fact("passport_count", len(rows))]
    items = []
    for p in rows:
        items.append(
            {
                "passport_id": p.id,
                "claim_ids": list(p.claim_ids),
                "issued": format_date(p.issued_at.date()),
                "expires": format_date(p.expires_at.date()),
                "expires_on": p.expires_at.date().isoformat(),
            }
        )
        facts += [_date_fact(f"passport {p.id} expires", p.expires_at.date()),
                  _count_fact(f"passport {p.id} claims", len(p.claim_ids))]  # fmt: skip
    return _result(ctx, "list_passports", {"passports": items}, facts)


def get_invoice(ctx: ToolContext, args: GetInvoiceArgs) -> ToolResult:
    """One of the user's own invoices. Someone else's invoice looks like a missing one."""
    inv = ctx.session.scalars(
        select(Invoice).where(Invoice.id == args.invoice_id, Invoice.user_id == ctx.user_id)
    ).first()
    if inv is None:
        return _result(ctx, "get_invoice", {"found": False}, [])
    payee = ctx.session.scalars(
        select(Payee).where(Payee.id == inv.beneficiary_payee_id, Payee.user_id == ctx.user_id)
    ).first()
    ccy = CurrencyCode(inv.currency)
    days_left = (inv.due_date - ctx.today).days
    data = {
        "found": True,
        "invoice_id": inv.id,
        "issuer": inv.issuer,
        "amount": format_minor(inv.amount_minor, ccy),
        "due_date": format_date(inv.due_date),
        "days_until_due": days_left,
        "status": inv.status,
        "beneficiary_payee_id": inv.beneficiary_payee_id,
        "beneficiary_name": (
            document_label(payee.account_no_masked)
            if payee
            and not ctx.raw_payee_names
            and (payee.provenance != "system" or payee.added_via == "document")
            else (payee.display_name if payee else None)
        ),
        "beneficiary_account_no": payee.account_no_masked if payee else None,
    }
    facts = [
        _money_fact("invoice amount", inv.amount_minor, inv.currency),
        _date_fact("invoice due date", inv.due_date),
        _count_fact("days until due", days_left)
        if days_left >= 0
        else _count_fact("days overdue", -days_left),
    ]
    return _result(ctx, "get_invoice", data, facts)


def list_invoices(ctx: ToolContext, args: NoArgs) -> ToolResult:
    """The user's own open invoices (the stored beneficiary is the trusted record, INV-4)."""
    rows = ctx.session.scalars(
        select(Invoice)
        .where(Invoice.user_id == ctx.user_id, Invoice.status == "open")
        .order_by(Invoice.due_date, Invoice.id)
    ).all()
    facts = [_count_fact("open_invoice_count", len(rows))]
    items = []
    for inv in rows:
        ccy = CurrencyCode(inv.currency)
        days_left = (inv.due_date - ctx.today).days
        items.append(
            {
                "invoice_id": inv.id,
                "issuer": inv.issuer,
                "description": inv.description,
                "amount": format_minor(inv.amount_minor, ccy),
                "due_date": format_date(inv.due_date),
                "days_until_due": days_left,
                "beneficiary_payee_id": inv.beneficiary_payee_id,
                "provenance": inv.provenance,
            }
        )
        facts += [
            _money_fact(f"invoice amount {inv.id}", inv.amount_minor, inv.currency),
            _date_fact(f"invoice due date {inv.id}", inv.due_date),
        ]
    return _result(ctx, "list_invoices", {"invoices": items}, facts)


def get_history(ctx: ToolContext, args: GetHistoryArgs) -> ToolResult:
    """The user's own recent transfers, newest first."""
    since = date.fromordinal(ctx.today.toordinal() - args.days)
    stmt = (
        select(Transfer)
        .where(Transfer.user_id == ctx.user_id)
        .order_by(Transfer.created_at.desc(), Transfer.id)
    )
    rows = [t for t in ctx.session.scalars(stmt) if t.created_at.date() >= since]
    if args.kind is not None:
        rows = [t for t in rows if t.route_json.get("type") == args.kind]
    total = len(rows)
    rows = rows[: args.limit]
    facts = [
        _count_fact("transfer_count", total),
        _count_fact("history days", args.days),
    ]
    items = []
    for t in rows:
        items.append(
            {
                "transfer_id": t.id,
                "date": format_date(t.created_at.date()),
                "payee_id": t.payee_id,
                "amount": format_minor(t.amount_minor, CurrencyCode(t.currency)),
                "status": t.status,
                "type": t.route_json.get("type"),
            }
        )
        facts += [
            _money_fact(f"transfer {t.id}", t.amount_minor, t.currency),
            _date_fact(f"transfer date {t.id}", t.created_at.date()),
        ]
    return _result(
        ctx,
        "get_history",
        {"transfers": items, "days": args.days, "total": total, "truncated": total > len(rows)},
        facts,
    )


def get_schedules(ctx: ToolContext, args: NoArgs) -> ToolResult:
    """The user's own recurring transfer schedules."""
    rows = ctx.session.scalars(
        select(Schedule).where(Schedule.user_id == ctx.user_id).order_by(Schedule.id)
    ).all()
    facts = [_count_fact("schedule_count", len(rows))]
    items = []
    for s in rows:
        tpl = s.proposal_template_json
        items.append(
            {
                "schedule_id": s.id,
                "payee_id": tpl.get("payee"),
                "amount": format_minor(int(tpl["amount_minor"]), CurrencyCode(tpl["currency"])),
                "rrule": s.rrule,
                "next_run": format_date(s.next_run),
                "status": s.status,
            }
        )
        facts += [
            _money_fact(f"schedule amount {s.id}", int(tpl["amount_minor"]), tpl["currency"]),
            _date_fact(f"next run {s.id}", s.next_run),
        ]
    return _result(ctx, "get_schedules", {"schedules": items}, facts)


def get_credit_summary(ctx: ToolContext, args: NoArgs) -> ToolResult:
    """On-time payment counts per category for the Credit Passport (no raw transactions)."""
    rows = ctx.session.scalars(
        select(CreditEvent).where(CreditEvent.user_id == ctx.user_id).order_by(CreditEvent.due_date)
    ).all()
    per: dict[str, dict[str, int]] = {}
    for e in rows:
        d = per.setdefault(e.kind, {"total": 0, "on_time": 0})
        d["total"] += 1
        d["on_time"] += int(e.on_time)
    months = 0
    if rows:
        first = rows[0].due_date
        months = (ctx.today.year - first.year) * 12 + ctx.today.month - first.month
    total = sum(d["total"] for d in per.values())
    on_time = sum(d["on_time"] for d in per.values())
    facts = [
        _count_fact("months of history", months),
        _count_fact("payments total", total),
        _count_fact("payments on time", on_time),
    ]
    cats = {}
    for kind, d in sorted(per.items()):
        cats[kind] = {**d, "late": d["total"] - d["on_time"]}
        facts += [
            _count_fact(f"{kind} total", d["total"]),
            _count_fact(f"{kind} on time", d["on_time"]),
        ]
    data = {
        "months_of_history": months,
        "categories": cats,
        "on_time_ratio_bps": (on_time * 10000 // total) if total else None,
    }
    return _result(ctx, "get_credit_summary", data, facts)


@dataclass(frozen=True)
class ReadTool:
    """A registered read tool: its spec for the planner, its args model and its function."""

    spec: ToolSpec
    args_model: type[_Args]
    fn: Callable[[ToolContext, Any], ToolResult]


def _tool(
    name: str, description: str, model: type[_Args], fn: Callable[[ToolContext, Any], ToolResult]
) -> ReadTool:
    schema = model.model_json_schema()
    return ReadTool(
        spec=ToolSpec(name=name, description=description, kind=ToolKind.READ, args_schema=schema),
        args_model=model,
        fn=fn,
    )


_READ_TOOLS: dict[str, ReadTool] = {
    t.spec.name: t
    for t in (
        _tool("get_balances", "List the user's account balances.", NoArgs, get_balances),
        _tool("get_payees", "List the user's saved payees (account numbers masked).", GetPayeesArgs, get_payees),
        _tool("get_fx_quote", "Indicative mid FX rate between two currencies, optionally converting an amount.", GetFxQuoteArgs, get_fx_quote),
        _tool("list_providers", "List active remittance providers and fees for a corridor.", ListProvidersArgs, list_providers),
        _tool("compare_routes", "Compare every remittance route for an amount, with fees, received amount and arrival.", CompareRoutesArgs, compare_routes),
        _tool("plan_tuition_funding", "Plan how to fund what is owed on an invoice from the user's accounts and family.", PlanTuitionArgs, plan_tuition_funding),
        _tool("list_passports", "List the user's Credit Passports and the claims each holds.", ListPassportsArgs, list_passports),
        _tool("get_invoice", "Get one of the user's invoices by id, with its stored beneficiary.", GetInvoiceArgs, get_invoice),
        _tool("list_invoices", "List the user's open invoices with amounts and due dates.", NoArgs, list_invoices),
        _tool("get_history", "List the user's recent transfers.", GetHistoryArgs, get_history),
        _tool("get_schedules", "List the user's recurring transfer schedules.", NoArgs, get_schedules),
        _tool("get_credit_summary", "Summarise on-time payment history for the Credit Passport.", NoArgs, get_credit_summary),
    )
}  # fmt: skip


READ_TOOLS: Mapping[str, ReadTool] = MappingProxyType(_READ_TOOLS)

CODE_RUN_TOOLS = frozenset({"compare_routes", "plan_tuition_funding", "list_passports"})
"""Read tools that only trusted code calls (P3-1). They are never offered to a planner or to the
baseline's model and a planner output naming one is invalid: the routes, the funding plan and the passport
list are computed by deterministic code, not chosen by a model."""


def tool_specs() -> list[ToolSpec]:
    """The READ tool specs the planner may be offered."""
    return [t.spec for t in READ_TOOLS.values()]


def call_tool(ctx: ToolContext, name: str, raw_args: dict[str, Any]) -> ToolResult:
    """Validate ``raw_args`` against the tool's args model and run it. Unknown tool or bad
    arguments (including any attempt to pass ``user_id``) raise ToolError (fail closed)."""
    tool = READ_TOOLS.get(name)
    if tool is None:
        raise ToolError(f"unknown tool {name}")
    # INV-1: refuse any session that is not a READ ONLY transaction (for example a writable one).
    if ctx.session.execute(text("SHOW transaction_read_only")).scalar() != "on":
        raise ToolError("read tools require a read-only session")
    try:
        args = tool.args_model.model_validate(raw_args)
    except ValidationError as exc:
        raise ToolError(f"invalid arguments for {name}") from exc
    return tool.fn(ctx, args)
