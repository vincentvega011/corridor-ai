"""Build an ``ActionProposal`` from trusted inputs only (docs/spec/modules.md 5.15 step 8, INV-1, INV-4).

Every value comes from our own code:
- amount: the user's evidence backed amount (``USER_TYPED``) or, with no evidence, ``MODEL_INFERRED``;
  for an invoice, the stored invoice amount (``SYSTEM``);
- payee: the resolved stored payee (``SYSTEM``), or the invoice's stored beneficiary;
- source account: chosen by currency from the user's own accounts (``SYSTEM``);
- idempotency key and proposal id: generated here;
- route: the cheapest single channel that meets the deadline (routing.optimized_route, ADR 0013).
Nothing is read from the planner's draft except that it agrees (see planning.validate_plan).
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from corridor.agent.routing import (
    RoutingError,
    mid_rate,
    optimized_route,
    route_summary,
)
from corridor.agent.settings import CorridorConfig
from corridor.contracts.action import (
    ActionProposal,
    ActionType,
    ContributionSpec,
    NewPayeeSpec,
    PassportShareSpec,
)
from corridor.contracts.money import CurrencyCode, Money
from corridor.contracts.provenance import Provenance, Tainted, lowest_trust
from corridor.contracts.routing import RouteRecommendation
from corridor.contracts.tools import ToolResult
from corridor.formatting import format_minor
from corridor.nlu.typed_payee import REGION_OF
from corridor.sandbox.doc_payee import NAME_NOTE, DocPayeeTerms, document_label


class ProposalError(Exception):
    """The request cannot become a proposal. ``code`` is machine readable, ``details`` is prompt safe."""

    def __init__(self, code: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class MoneyPlan:
    """Which account pays, in which currency, and how the amount is interpreted."""

    src: CurrencyCode
    dst: CurrencyCode
    mode: str  # send (amount is what leaves) | deliver (amount is what arrives) | domestic
    source_account_id: str

    @property
    def cross_border(self) -> bool:
        return self.src is not self.dst


def decide_money(
    *, payee_currency: str, amount_currency: str | None, home_region: str, accounts: list[dict[str, Any]], corridors: CorridorConfig
) -> MoneyPlan:  # fmt: skip
    """Pick the source account and the interpretation of the user's amount (see module docstring)."""
    by_ccy = {
        a["currency"]: a["account_id"]
        for a in accounts
        if a.get("status", "active") == "active" and a.get("kind", "spending") == "spending"
    }
    dst = CurrencyCode(payee_currency)
    amt = CurrencyCode(amount_currency) if amount_currency else dst
    if amt is not dst:
        if amt.value not in by_ccy:
            raise ProposalError("no_account", {"currency": amt.value})
        if f"{amt.value}/{dst.value}" not in corridors.default_providers:
            raise ProposalError("no_route", {"reason": f"no corridor {amt.value} to {dst.value}"})
        return MoneyPlan(amt, dst, "send", by_ccy[amt.value])
    home = corridors.home_currency.get(home_region)
    if (
        home
        and home != dst.value
        and home in by_ccy
        and f"{home}/{dst.value}" in corridors.default_providers
    ):
        return MoneyPlan(CurrencyCode(home), dst, "deliver", by_ccy[home])
    if dst.value in by_ccy:
        return MoneyPlan(dst, dst, "domestic", by_ccy[dst.value])
    raise ProposalError("no_account", {"currency": dst.value})


def _acct_label(balances: ToolResult, account_id: str) -> str:
    """An account in words (currency, kind, masked number), never its id."""
    for a in balances.data.get("accounts", []):
        if a.get("account_id") == account_id:
            return str(a.get("label") or account_id)
    return account_id


def _balance(balances: ToolResult, account_id: str) -> int:
    for f in balances.facts:
        if f.label == f"balance {account_id}" and f.amount_minor is not None:
            return f.amount_minor
    raise ProposalError("no_account", {"account": account_id})


@dataclass(frozen=True)
class ProposalBuild:
    """The proposal plus what the confirmation card shows."""

    proposal: ActionProposal
    display: dict[str, Any]
    route: dict[str, Any] | None
    warnings: tuple[str, ...]


def compute_route(
    *, money: MoneyPlan, amount_minor: int, providers: ToolResult | None, quote: ToolResult | None,
    corridors: CorridorConfig, hours_until_deadline: int | None = None,
) -> tuple[RouteRecommendation, str]:  # fmt: skip
    """The cheapest on time route for a cross border transfer, and the provider's name."""
    if providers is None or quote is None:
        raise ProposalError("no_route", {"reason": "route data missing"})
    pid = corridors.default_providers.get(f"{money.src.value}/{money.dst.value}")
    try:
        mid = mid_rate(quote)
        kw: dict[str, Any] = {
            "hours_until_deadline": hours_until_deadline,
            "default_provider_id": pid,
        }
        if money.mode == "deliver":
            return optimized_route(
                providers, mid, money.src, money.dst, deliver_minor=amount_minor, **kw
            )
        return optimized_route(providers, mid, money.src, money.dst, send_minor=amount_minor, **kw)
    except RoutingError as exc:
        raise ProposalError("no_route", {"reason": str(exc)}) from exc


def build_transfer(
    *, user_id: str, money: MoneyPlan, amount_slot: dict[str, Any], payee: dict[str, Any],
    balances: ToolResult, route: RouteRecommendation | None, provider_name: str | None,
    deadline: dict[str, Any] | None, doc_hashes: tuple[str, ...] = (), rate_date: str | None = None,
) -> ProposalBuild:  # fmt: skip
    """A TRANSFER proposal. ``amount_slot`` is the IntentFrame amount ({value, provenance})."""
    amount_minor = int(amount_slot["value"])
    prov = Provenance(amount_slot["provenance"])
    send_minor = route.legs[0].send_amount.amount_minor if route is not None else amount_minor
    balance = _balance(balances, money.source_account_id)
    if balance < send_minor:
        raise ProposalError(
            "insufficient_funds",
            {
                "balance": format_minor(balance, money.src),
                "needed": format_minor(send_minor, money.src),
            },
        )
    amount = Tainted[Money](
        value=Money(amount_minor=send_minor, currency=money.src),
        provenance=lowest_trust(prov, Provenance.SYSTEM),
        source_ref=amount_slot.get("source_ref"),
    )
    proposal = ActionProposal(
        proposal_id=f"prop_{uuid.uuid4().hex[:16]}",
        action=ActionType.TRANSFER,
        user_id=user_id,
        source_account_id=Tainted[str](value=money.source_account_id, provenance=Provenance.SYSTEM, source_ref="get_balances"),
        payee_id=Tainted[str](value=str(payee["payee_id"]), provenance=Provenance.SYSTEM, source_ref="get_payees"),
        amount=amount,
        route=route,
        derived_from_docs=doc_hashes,
        idempotency_key=f"idem-{uuid.uuid4().hex}",
    )  # fmt: skip
    display = {
        "action": "transfer",
        "amount": format_minor(send_minor, money.src),
        **_payee_display(payee),
        "from_account": _acct_label(balances, money.source_account_id),
        "deadline": (deadline or {}).get("date"),
        "amount_source": "from your words" if prov is Provenance.USER_TYPED else "inferred by the assistant, please check",
    }  # fmt: skip
    warnings = ("amount_not_stated_by_user",) if prov is not Provenance.USER_TYPED else ()
    return ProposalBuild(
        proposal,
        display,
        (route_summary(route, provider_name or "") | {"rate_date": rate_date}) if route else None,
        warnings,
    )


def build_invoice_payment(
    *, user_id: str, invoice_id: str, invoices: ToolResult, payees: ToolResult, balances: ToolResult,
    document: dict[str, Any] | None = None, doc_hash: str | None = None,
    doc_hashes: tuple[str, ...] = (), account_id: str | None = None, pay_minor: int | None = None,
) -> ProposalBuild:  # fmt: skip
    """A PAY_INVOICE proposal. Amount and payee come from the stored invoice, never from the user's
    words or a model (INV-4): the stored beneficiary is the trusted record.

    ``account_id`` and ``pay_minor`` make an instalment of a funding plan (ADR 0014): both come from the
    deterministic planner, never from a model or the user's words, and policy still caps the instalment at
    what is owed."""
    item = next(
        (i for i in invoices.data.get("invoices", []) if i["invoice_id"] == invoice_id), None
    )
    if item is None:
        raise ProposalError("invoice_not_found")
    fact = next((f for f in invoices.facts if f.label == f"invoice amount {invoice_id}"), None)
    if fact is None or fact.amount_minor is None or fact.currency is None:
        raise ProposalError("invoice_not_found")
    payee = next(
        (p for p in payees.data.get("payees", []) if p["payee_id"] == item["beneficiary_payee_id"]),
        None,
    )
    if payee is None:
        raise ProposalError("invoice_not_found")
    acct = next(
        (
            a
            for a in balances.data.get("accounts", [])
            if a["currency"] == fact.currency.value
            and (
                a["account_id"] == account_id
                if account_id
                else a.get("kind", "spending") == "spending"
            )
        ),
        None,
    )
    if acct is None:
        raise ProposalError("no_account", {"currency": fact.currency.value})
    to_pay = fact.amount_minor if pay_minor is None else pay_minor
    balance = _balance(balances, acct["account_id"])
    if balance < to_pay:
        raise ProposalError(
            "insufficient_funds",
            {
                "balance": format_minor(balance, fact.currency),
                "needed": format_minor(to_pay, fact.currency),
            },
        )
    payee_id = Tainted[str](
        value=str(payee["payee_id"]), provenance=Provenance.SYSTEM, source_ref="list_invoices"
    )
    amount = Tainted[Money](
        value=Money(amount_minor=to_pay, currency=fact.currency),
        provenance=Provenance.SYSTEM, source_ref="list_invoices",
    )  # fmt: skip
    warnings: tuple[str, ...] = ()
    if document is not None and doc_hash is not None:
        # The user attached a document and said "pay this". The stored invoice is the trusted record;
        # what the document claims is cross-checked, never trusted (INV-4).
        if document.get("beneficiary_matches_stored") is False and document.get("beneficiary_hash"):
            # the document wants the money to go to another account: carry that account as untrusted
            # so the policy engine denies it (R-TAINT-PAYEE, R-INVOICE-BENEFICIARY, R-INJECTION)
            payee_id = Tainted[str](
                value="doc_acct_" + str(document["beneficiary_hash"])[:16],
                provenance=Provenance.UNTRUSTED, source_ref=doc_hash[:16],
            )  # fmt: skip
            warnings += ("document_account_differs",)
        d_minor, d_ccy = document.get("amount_minor"), document.get("currency")
        if document.get("amount_matches_stored") is False and isinstance(d_minor, int) and d_ccy:
            amount = Tainted[Money](
                value=Money(amount_minor=d_minor, currency=CurrencyCode(d_ccy)),
                provenance=Provenance.UNTRUSTED, source_ref=doc_hash[:16],
            )  # fmt: skip
            warnings += ("document_amount_differs",)
    proposal = ActionProposal(
        proposal_id=f"prop_{uuid.uuid4().hex[:16]}",
        action=ActionType.PAY_INVOICE,
        user_id=user_id,
        source_account_id=Tainted[str](value=acct["account_id"], provenance=Provenance.SYSTEM, source_ref="get_balances"),
        payee_id=payee_id,
        amount=amount,
        invoice_id=invoice_id,
        derived_from_docs=tuple(dict.fromkeys((*doc_hashes, *((doc_hash,) if doc_hash else ())))),
        idempotency_key=f"idem-{uuid.uuid4().hex}",
    )  # fmt: skip
    display = {
        "action": "pay_invoice",
        "amount": format_minor(to_pay, fact.currency),
        "to": f"{payee['display_name']} {payee['account_no']}",
        "from_account": _acct_label(balances, acct["account_id"]),
        "invoice": (
            f"{item['issuer']}, {item['description']} (due {item['due_date']})"
            if item.get("description")
            else f"{item['issuer']}, due {item['due_date']}"
        ),
        "amount_source": "stored invoice" if pay_minor is None else "funding plan instalment",
    }  # fmt: skip
    if pay_minor is not None and pay_minor != fact.amount_minor:
        display["instalment_of"] = format_minor(fact.amount_minor, fact.currency)
    return ProposalBuild(proposal, display, None, warnings)


def _who(step: dict[str, Any]) -> str:
    """A contributor by name and relation, for example "Ramesh (uncle, QA)"."""
    parts = [p for p in (step.get("from_relation"), step.get("from_region")) if p]
    name = step.get("from_name") or "your family member"
    return f"{name} ({', '.join(parts)})" if parts else str(name)


def build_contribution_request(
    *, user_id: str, step: dict[str, Any], invoice_id: str
) -> ProposalBuild:
    """A REQUEST_CONTRIBUTION proposal from one step of the funding plan (numbers from the planner).

    It asks a linked family member for money and moves nothing: the contributor decides in their own
    account (INV-1). Every value is system derived from the deterministic plan."""
    ccy = CurrencyCode(step["request_currency"])
    proposal = ActionProposal(
        proposal_id=f"prop_{uuid.uuid4().hex[:16]}",
        action=ActionType.REQUEST_CONTRIBUTION,
        user_id=user_id,
        source_account_id=Tainted[str](
            value=step["target_account"], provenance=Provenance.SYSTEM, source_ref="plan_tuition_funding"
        ),
        amount=Tainted[Money](
            value=Money(amount_minor=int(step["request_minor"]), currency=ccy),
            provenance=Provenance.SYSTEM, source_ref="plan_tuition_funding",
        ),
        invoice_id=invoice_id,
        contribution=ContributionSpec(
            contributor_user_id=step["from_user"], target_account_id=step["target_account"]
        ),
        idempotency_key=f"idem-{uuid.uuid4().hex}",
    )  # fmt: skip
    display = {
        "action": "request_contribution",
        "amount": step["request"],
        "to": f"{_who(step)} (a request, no money moves from you)",
        "from_account": f"into {step.get('target_account_label') or step['target_account']}",
        "arrives": f"{step['arrives']} via {step['route']}, about {step['eta_hours']} hours after they agree",
        "amount_source": "funding plan",
    }  # fmt: skip
    return ProposalBuild(proposal, display, None, ())


def _spending_account(balances: ToolResult) -> str:
    acct = next(
        (a for a in balances.data.get("accounts", []) if a.get("kind", "spending") == "spending"),
        None,
    )
    if acct is None:
        raise ProposalError("no_account", {"currency": "-"})
    return str(acct["account_id"])


def build_passport_issue(
    *, user_id: str, balances: ToolResult, claim_preview: tuple[str, ...] = ()
) -> ProposalBuild:
    """An ISSUE_PASSPORT proposal. The claims are computed by the executor from the user's own
    history at execution time, so nothing from the user's words or a model can enter a claim."""
    proposal = ActionProposal(
        proposal_id=f"prop_{uuid.uuid4().hex[:16]}",
        action=ActionType.ISSUE_PASSPORT,
        user_id=user_id,
        source_account_id=Tainted[str](
            value=_spending_account(balances), provenance=Provenance.SYSTEM, source_ref="get_balances"
        ),
        idempotency_key=f"idem-{uuid.uuid4().hex}",
    )  # fmt: skip
    display: dict[str, Any] = {
        "action": "issue_passport",
        "what": "A signed Credit Passport from your payment history: separate claims (counts only, no amounts, no account numbers) that you can share one by one.",
        "valid_for": "90 days",
    }  # fmt: skip
    if claim_preview:  # what the passport would hold now; recomputed at execution, counts only
        display["will_contain"] = list(claim_preview)
    return ProposalBuild(proposal, display, None, ())


def build_passport_share(
    *, user_id: str, balances: ToolResult, passport_id: str, claim_ids: tuple[str, ...],
    audience: str, statements: dict[str, str], nonce: str | None = None,
) -> ProposalBuild:  # fmt: skip
    """A SHARE_PASSPORT proposal: exactly these claims of this passport for this audience, answering
    the verifier's challenge ``nonce`` when there is one (it is part of what the user confirms)."""
    proposal = ActionProposal(
        proposal_id=f"prop_{uuid.uuid4().hex[:16]}",
        action=ActionType.SHARE_PASSPORT,
        user_id=user_id,
        source_account_id=Tainted[str](
            value=_spending_account(balances), provenance=Provenance.SYSTEM, source_ref="get_balances"
        ),
        passport_share=PassportShareSpec(
            passport_id=passport_id, claim_ids=claim_ids, audience=audience, nonce=nonce
        ),
        idempotency_key=f"idem-{uuid.uuid4().hex}",
    )  # fmt: skip
    display = {
        "action": "share_passport",
        "shares_only": [statements.get(c, c) for c in claim_ids],
        "for": audience,
        "hidden": "all other claims, all amounts and all account numbers",
    }  # fmt: skip
    return ProposalBuild(proposal, display, None, ())


def _payee_display(payee: dict[str, Any]) -> dict[str, Any]:
    """How the card names a payee. A payee from a document shows the fixed label, then the sanitised name
    taken from the document with a note that it is not verified (deterministic rendering, never a prompt)."""
    if payee.get("name_untrusted"):
        out: dict[str, Any] = {"to": str(payee["display_name"]), "name_note": NAME_NOTE}
        if payee.get("match_name"):
            out["document_name"] = str(payee["match_name"])
        return out
    return {"to": f"{payee['display_name']} {payee['account_no']}"}


def build_add_payee(
    *, user_id: str, terms: DocPayeeTerms, doc_hash: str, balances: ToolResult
) -> ProposalBuild:
    """An ADD_PAYEE proposal for the account a document asks the user to pay.

    Every term of the payee is ``UNTRUSTED`` (it came from a document) and the full account number is never
    held: the proposal carries a placeholder that the executor checks against the stored document (INV-4,
    INV-12). The payee will be held by R-PAYEE-COOLOFF whatever the user confirms."""
    acct = next(
        (
            a
            for a in balances.data.get("accounts", [])
            if a["currency"] == terms.currency and a.get("kind", "spending") == "spending"
        ),
        None,
    ) or next(iter(balances.data.get("accounts", [])), None)
    if acct is None:
        raise ProposalError("no_account", {"currency": terms.currency})
    ref = doc_hash[:16]

    def untrusted(value: str) -> Tainted[Any]:
        return Tainted[Any](value=value, provenance=Provenance.UNTRUSTED, source_ref=ref)

    proposal = ActionProposal(
        proposal_id=f"prop_{uuid.uuid4().hex[:16]}",
        action=ActionType.ADD_PAYEE,
        user_id=user_id,
        source_account_id=Tainted[str](
            value=acct["account_id"], provenance=Provenance.SYSTEM, source_ref="get_balances"
        ),
        new_payee=NewPayeeSpec(
            display_name=untrusted(terms.name),
            region=untrusted(terms.region),
            currency=CurrencyCode(terms.currency),
            account_no=untrusted(terms.account_ref),
        ),
        derived_from_docs=(doc_hash,),
        idempotency_key=f"idem-{uuid.uuid4().hex}",
    )
    display = {
        "action": "add_payee",
        "to": document_label(f"****{terms.last4}"),
        "document_name": terms.name,
        "name_note": NAME_NOTE,
        "currency": terms.currency,
        "from_account": str(acct.get("label") or acct["account_id"]),
        "source": "taken from an attached document, not verified",
    }
    return ProposalBuild(proposal, display, None, ("payee_from_document",))


def build_add_typed_payee(
    *,
    user_id: str,
    name: tuple[str, Provenance],
    account_hash: str,
    last4: str,
    account_prov: Provenance,
    currency: str,
    balances: ToolResult,
) -> ProposalBuild:
    """An ADD_PAYEE proposal for a payee the user typed in chat (ADR 0018).

    The proposal carries the keyed hash and last four digits of the account, never the number
    (INV-12). Each field keeps the provenance code found for it: ``USER_TYPED`` only when the
    digits were in the user's own words, ``UNTRUSTED`` when they came from pasted text,
    ``MODEL_INFERRED`` when a model supplied them. The executor stores the least trusted of the two."""
    acct = next(
        (
            a
            for a in balances.data.get("accounts", [])
            if a["currency"] == currency and a.get("kind", "spending") == "spending"
        ),
        None,
    ) or next(iter(balances.data.get("accounts", [])), None)
    if acct is None:
        raise ProposalError("no_account", {"currency": currency})
    ref = "typed"

    def field(value: str, prov: Provenance) -> Tainted[Any]:
        return Tainted[Any](value=value, provenance=prov, source_ref=ref)

    proposal = ActionProposal(
        proposal_id=f"prop_{uuid.uuid4().hex[:16]}",
        action=ActionType.ADD_PAYEE,
        user_id=user_id,
        source_account_id=Tainted[str](
            value=acct["account_id"], provenance=Provenance.SYSTEM, source_ref="get_balances"
        ),
        new_payee=NewPayeeSpec(
            display_name=field(name[0], name[1]),
            region=field(REGION_OF.get(currency, "XX"), Provenance.SYSTEM),
            currency=CurrencyCode(currency),
            account_no=field(f"typed:{account_hash}:{last4}", account_prov),
        ),
        idempotency_key=f"idem-{uuid.uuid4().hex}",
    )
    origin = {
        Provenance.USER_TYPED: "typed by you",
        Provenance.UNTRUSTED: "taken from pasted text, not verified",
        Provenance.MODEL_INFERRED: "guessed by the assistant, not verified",
    }[account_prov]
    display = {
        "action": "add_payee",
        "to": f"****{last4}",
        "name": name[0],
        "currency": currency,
        "from_account": str(acct.get("label") or acct["account_id"]),
        "source": origin,
        "hold": "payments above the first payment cap wait 24 hours unless the bank verifies the payee",
    }
    warns = ("payee_untrusted",) if account_prov is not Provenance.USER_TYPED else ()
    return ProposalBuild(proposal, display, None, warns)


def proposal_card(build: ProposalBuild) -> dict[str, Any]:
    """The exact hashed fields plus the human readable display, for the confirm_request event."""
    p = build.proposal
    return {
        "hash": p.canonical_hash(),
        "hashed_fields": p.model_dump(mode="json", include={"action", "user_id", "invoice_id", "idempotency_key"})
        | {
            "source_account_id": p.source_account_id.value,
            "payee_id": p.payee_id.value if p.payee_id else None,
            "amount_minor_text": format_minor(p.amount.value.amount_minor, p.amount.value.currency) if p.amount else None,
            "route_legs": [leg.model_dump(mode="json") for leg in p.route.legs] if p.route else None,
        },
        "display": build.display,
        "route": build.route,
        "warnings": list(build.warnings),
        "amount_provenance": p.amount.provenance.value if p.amount else None,
    }  # fmt: skip


_ = Decimal  # keep the import used for type checkers
