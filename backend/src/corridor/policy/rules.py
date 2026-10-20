"""The policy rules (docs/spec/modules.md 5.7). Each rule is a small pure function with a stable id.

A rule returns zero or more ``RuleResult``s. It never reads a clock, a file, a database or the
network: everything comes from ``(proposal, state, config, now)`` (INV-2). Rules cannot see each
other's results, so no rule can relax another (INV-7); the engine combines them with max.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, Decimal

from corridor.contracts.action import ActionProposal, ActionType
from corridor.contracts.money import CurrencyCode, Money, minor_unit_exponent
from corridor.contracts.policy import Decision
from corridor.contracts.provenance import Provenance, lowest_trust
from corridor.policy.config import PolicyConfig
from corridor.policy.state import AccountState

OUTGOING = frozenset(
    {ActionType.TRANSFER, ActionType.PAY_INVOICE, ActionType.CONVERT, ActionType.SCHEDULE}
)
"""Actions that move or commit money. Every one needs at least a confirmation."""
LOCKABLE = OUTGOING | {
    ActionType.ADD_PAYEE,
    ActionType.REQUEST_CONTRIBUTION,
    ActionType.SHARE_PASSPORT,
}
"""Actions refused while the safety lock is on (INV-11)."""
PAYEE_ACTIONS = frozenset({ActionType.TRANSFER, ActionType.PAY_INVOICE, ActionType.SCHEDULE})
"""Actions that pay a saved payee."""
DEBIT_NOW = frozenset({ActionType.TRANSFER, ActionType.PAY_INVOICE, ActionType.CONVERT})
"""Actions that debit an account immediately (balance is checked)."""
KNOWN_ACTIONS = frozenset(ActionType)
HIGH_RISK_RULES = frozenset(
    {
        "R-TAINT-PAYEE",
        "R-INVOICE-BENEFICIARY",
        "R-INJECTION",
        "R-RISK",
        "R-VELOCITY",
        "R-PAYEE-ADD",
        "R-PAYEE-COOLOFF",
        "R-OWNERSHIP",
        "R-SETTINGS",
    }
)
"""A denial by one of these rules counts toward the safety lock (INV-11). Ordinary denials such as
insufficient funds or a limit do not: they are not signs of an attack."""
TAINTED = frozenset({Provenance.UNTRUSTED, Provenance.MODEL_INFERRED})
"""Provenance values that are never treated as confirmed or as typed by the user."""


@dataclass(frozen=True)
class RuleResult:
    """One rule's verdict: its id, the decision it asks for and a reason key for the user text."""

    rule_id: str
    decision: Decision
    reason: str


Rule = Callable[[ActionProposal, AccountState, PolicyConfig, datetime], list[RuleResult]]


def to_npr_minor(money: Money, state: AccountState) -> int | None:
    """NPR minor units equivalent of ``money`` using the state's FX snapshot, rounded UP so a limit
    is never under counted. ``None`` when the snapshot has no rate (the caller fails closed)."""
    rate = state.fx_to_npr.get(money.currency)
    if rate is None:
        return None
    npr_exp = minor_unit_exponent(CurrencyCode.NPR)
    scale = Decimal(10) ** (npr_exp - minor_unit_exponent(money.currency))
    return int((Decimal(money.amount_minor) * rate * scale).to_integral_value(ROUND_CEILING))


def _payee_provenance(p: ActionProposal, s: AccountState) -> Provenance | None:
    if p.payee_id is None:
        return None
    stored = s.payees.get(p.payee_id.value)
    if stored is None:
        return p.payee_id.provenance
    return lowest_trust(p.payee_id.provenance, stored.provenance)


def _amount_provenance(p: ActionProposal, s: AccountState) -> Provenance | None:
    if p.amount is None:
        return None
    if p.invoice_id is not None and p.invoice_id in s.invoices:
        return lowest_trust(p.amount.provenance, s.invoices[p.invoice_id].provenance)
    return p.amount.provenance


def r_lock(p: ActionProposal, s: AccountState, c: PolicyConfig, now: datetime) -> list[RuleResult]:
    """R-LOCK: while the session is locked every outgoing action is denied (INV-11)."""
    if s.locked and p.action in LOCKABLE:
        return [RuleResult("R-LOCK", Decision.DENY, "locked")]
    return []


def r_settings(
    p: ActionProposal, s: AccountState, c: PolicyConfig, now: datetime
) -> list[RuleResult]:
    """R-SETTINGS: a budget that names a limit, MFA or contact detail is a settings change."""
    if p.action is ActionType.SET_BUDGET and p.budget is not None:
        text = p.budget.category.casefold()
        if any(w.casefold() in text for w in c.protected_setting_words):
            return [RuleResult("R-SETTINGS", Decision.DENY, "settings_change")]
    return []


def r_ownership(
    p: ActionProposal, s: AccountState, c: PolicyConfig, now: datetime
) -> list[RuleResult]:
    """R-OWNERSHIP: account, payee and invoice must belong to the user and be usable."""
    out: list[RuleResult] = []
    if p.action in OUTGOING or p.action is ActionType.CANCEL_SCHEDULE:
        acct = s.accounts.get(p.source_account_id.value)
        if acct is None or not acct.active:
            out.append(RuleResult("R-OWNERSHIP", Decision.DENY, "account_not_usable"))
    if p.action in PAYEE_ACTIONS and (p.payee_id is None or p.payee_id.value not in s.payees):
        out.append(RuleResult("R-OWNERSHIP", Decision.DENY, "payee_not_found"))
    if p.action is ActionType.PAY_INVOICE and (
        p.invoice_id is None or p.invoice_id not in s.invoices
    ):
        out.append(RuleResult("R-OWNERSHIP", Decision.DENY, "invoice_not_found"))
    return out


def r_invoice(
    p: ActionProposal, s: AccountState, c: PolicyConfig, now: datetime
) -> list[RuleResult]:
    """R-INVOICE-BENEFICIARY: an invoice is paid only to its stored beneficiary, in its currency,
    and never more than is still owed. An instalment (a part of the outstanding amount) is allowed
    (P3-1, ADR 0014): the money still goes only to the stored beneficiary and the total can never
    exceed the invoice."""
    if p.action is not ActionType.PAY_INVOICE or p.invoice_id not in s.invoices:
        return []
    inv = s.invoices[p.invoice_id]
    out: list[RuleResult] = []
    if p.payee_id is None or p.payee_id.value != inv.beneficiary_payee_id:
        out.append(RuleResult("R-INVOICE-BENEFICIARY", Decision.DENY, "beneficiary_differs"))
        if p.payee_id is not None and _payee_provenance(p, s) in TAINTED:
            out.append(RuleResult("R-TAINT-PAYEE", Decision.DENY, "tainted_beneficiary_differs"))
    if (
        p.amount is None
        or p.amount.value.currency is not inv.currency
        or not 0 < p.amount.value.amount_minor <= inv.amount_minor - inv.paid_minor
    ):
        out.append(RuleResult("R-INVOICE-BENEFICIARY", Decision.DENY, "invoice_amount_differs"))
    return out


def r_contribution(
    p: ActionProposal, s: AccountState, c: PolicyConfig, now: datetime
) -> list[RuleResult]:
    """R-CONTRIBUTION: a request for money goes only to a linked family member, for no more than the
    link allows, in the contributor's currency, into one of the user's own active accounts. It moves
    no money (the contributor decides), so the decision is a confirmation at most."""
    if p.action is not ActionType.REQUEST_CONTRIBUTION or p.contribution is None:
        return []
    link = s.family_links.get(p.contribution.contributor_user_id)
    if link is None:
        return [RuleResult("R-CONTRIBUTION", Decision.DENY, "no_family_link")]
    if link.requests_last_24h >= c.contribution.max_requests_per_contributor_per_day:
        # asking the same person again and again is pressure, not a plan: a daily cap per
        # contributor
        return [RuleResult("R-CONTRIBUTION", Decision.DENY, "contribution_daily_cap")]
    out: list[RuleResult] = []
    target = s.accounts.get(p.contribution.target_account_id)
    if target is None or not target.active:
        out.append(RuleResult("R-OWNERSHIP", Decision.DENY, "account_not_usable"))
    if p.amount is None or p.amount.value.currency is not link.currency:
        out.append(RuleResult("R-CONTRIBUTION", Decision.DENY, "contribution_currency"))
    elif p.amount.value.amount_minor > link.per_request_limit_minor:
        out.append(RuleResult("R-CONTRIBUTION", Decision.DENY, "contribution_over_limit"))
    return out


def r_passport(
    p: ActionProposal, s: AccountState, c: PolicyConfig, now: datetime
) -> list[RuleResult]:
    """R-PASSPORT: a passport is issued only for a user with payment history, and shared only from
    the user's own unexpired passport, only the claims it really holds, with a named audience.
    Sharing sends personal data to someone else, so it needs MFA (see ``r_tier``)."""
    if p.action is ActionType.ISSUE_PASSPORT:
        if s.credit_event_count == 0:
            return [RuleResult("R-PASSPORT", Decision.DENY, "no_payment_history")]
        return []
    if p.action is not ActionType.SHARE_PASSPORT or p.passport_share is None:
        return []
    spec = p.passport_share
    held = s.passports.get(spec.passport_id)
    if held is None:
        return [RuleResult("R-PASSPORT", Decision.DENY, "passport_not_found")]
    out: list[RuleResult] = []
    if held.expires_at <= now:
        out.append(RuleResult("R-PASSPORT", Decision.DENY, "passport_expired"))
    if not set(spec.claim_ids) <= held.claim_ids:
        out.append(RuleResult("R-PASSPORT", Decision.DENY, "claim_not_held"))
    if not spec.audience.strip():
        out.append(RuleResult("R-PASSPORT", Decision.DENY, "audience_missing"))
    return out


def r_currency(
    p: ActionProposal, s: AccountState, c: PolicyConfig, now: datetime
) -> list[RuleResult]:
    """R-CURRENCY: the corridor must exist and the numbers must be in the account's currency."""
    out: list[RuleResult] = []
    if p.amount is None:
        return out
    src = p.amount.value.currency
    acct = s.accounts.get(p.source_account_id.value)
    if p.action in OUTGOING and acct is not None and acct.currency is not src:
        out.append(RuleResult("R-CURRENCY", Decision.DENY, "account_currency_mismatch"))
    if p.action in PAYEE_ACTIONS and p.payee_id is not None and p.payee_id.value in s.payees:
        dst = s.payees[p.payee_id.value].currency
        if src is not dst and f"{src.value}/{dst.value}" not in s.supported_pairs:
            out.append(RuleResult("R-CURRENCY", Decision.DENY, "corridor_unsupported"))
        if p.route is not None and p.route.delivered.currency is not dst:
            out.append(RuleResult("R-CURRENCY", Decision.DENY, "route_currency_mismatch"))
        if p.route is None and src is not dst:
            out.append(RuleResult("R-CURRENCY", Decision.DENY, "route_missing"))
    if p.action is ActionType.CONVERT:
        target = p.target_currency
        if target is None or f"{src.value}/{target.value}" not in s.convert_pairs:
            out.append(RuleResult("R-CURRENCY", Decision.DENY, "pair_unsupported"))
    if to_npr_minor(p.amount.value, s) is None:
        out.append(RuleResult("R-CURRENCY", Decision.DENY, "no_fx_rate"))
    return out


def r_balance(
    p: ActionProposal, s: AccountState, c: PolicyConfig, now: datetime
) -> list[RuleResult]:
    """R-BALANCE: insufficient funds are denied with an explanation."""
    if p.action in DEBIT_NOW and p.amount is not None:
        acct = s.accounts.get(p.source_account_id.value)
        if acct is not None and acct.balance_minor < p.amount.value.amount_minor:
            return [RuleResult("R-BALANCE", Decision.DENY, "insufficient_funds")]
    return []


def r_daily_limit(
    p: ActionProposal, s: AccountState, c: PolicyConfig, now: datetime
) -> list[RuleResult]:
    """R-DAILY-LIMIT: outgoing in the last 24 hours plus this action stays in the NPR limit."""
    if p.action not in OUTGOING or p.amount is None:
        return []
    this = to_npr_minor(p.amount.value, s)
    earlier = [to_npr_minor(m, s) for m in s.outgoing_today]
    if this is None:
        return []  # R-CURRENCY already denies a missing rate
    if any(e is None for e in earlier):
        return [RuleResult("R-DAILY-LIMIT", Decision.DENY, "daily_total_unknown")]
    total = this + sum(e for e in earlier if e is not None)
    if total > c.limits.daily_npr_minor:
        return [RuleResult("R-DAILY-LIMIT", Decision.DENY, "daily_limit_exceeded")]
    return []


def r_velocity(
    p: ActionProposal, s: AccountState, c: PolicyConfig, now: datetime
) -> list[RuleResult]:
    """R-VELOCITY: many transfers in a short window need MFA, then are denied."""
    if p.action not in {ActionType.TRANSFER, ActionType.PAY_INVOICE}:
        return []
    start = now - timedelta(minutes=c.velocity.window_minutes)
    n = 1 + sum(1 for t in s.recent_transfer_times if start <= t <= now)  # this one counts too
    if n > c.velocity.deny_over:
        return [RuleResult("R-VELOCITY", Decision.DENY, "velocity_deny")]
    if n > c.velocity.mfa_over:
        return [RuleResult("R-VELOCITY", Decision.REQUIRE_MFA, "velocity_mfa")]
    return []


def r_taint_payee(
    p: ActionProposal, s: AccountState, c: PolicyConfig, now: datetime
) -> list[RuleResult]:
    """R-TAINT-PAYEE: a payee or account from an untrusted or model made source needs MFA."""
    out: list[RuleResult] = []
    if p.action in PAYEE_ACTIONS and _payee_provenance(p, s) in TAINTED:
        out.append(RuleResult("R-TAINT-PAYEE", Decision.REQUIRE_MFA, "tainted_payee"))
    if p.action in OUTGOING and p.source_account_id.provenance in TAINTED:
        out.append(RuleResult("R-TAINT-PAYEE", Decision.REQUIRE_MFA, "tainted_source_account"))
    return out


def r_taint_amount(
    p: ActionProposal, s: AccountState, c: PolicyConfig, now: datetime
) -> list[RuleResult]:
    """R-TAINT-AMOUNT: an untrusted amount needs a confirmation; a model inferred one needs MFA."""
    prov = _amount_provenance(p, s)
    if prov is Provenance.MODEL_INFERRED:
        return [RuleResult("R-TAINT-AMOUNT", Decision.REQUIRE_MFA, "inferred_amount")]
    if prov is Provenance.UNTRUSTED:
        return [RuleResult("R-TAINT-AMOUNT", Decision.REQUIRE_CONFIRM, "untrusted_amount")]
    return []


def r_injection(
    p: ActionProposal, s: AccountState, c: PolicyConfig, now: datetime
) -> list[RuleResult]:
    """R-INJECTION: a proposal derived from a document flagged as an injection needs MFA."""
    if any(h in s.flagged_docs for h in p.derived_from_docs):
        return [RuleResult("R-INJECTION", Decision.REQUIRE_MFA, "injection_suspected")]
    return []


def r_risk(p: ActionProposal, s: AccountState, c: PolicyConfig, now: datetime) -> list[RuleResult]:
    """R-RISK: a high risk score needs MFA; a critical one is denied (and locks the session).

    The score is the larger of the one the proposal carries and the deterministic rule score
    (``corridor.risk``). Both can only add a restriction, so this rule never relaxes another
    (INV-7)."""
    from corridor.risk.score import assess

    score = max(p.risk.score_bps if p.risk is not None else 0, assess(p, s, c, now).score_bps)
    if score >= c.risk.critical_bps:
        return [RuleResult("R-RISK", Decision.DENY, "risk_critical")]
    if score >= c.risk.high_bps:
        return [RuleResult("R-RISK", Decision.REQUIRE_MFA, "risk_high")]
    return []


def r_payee_add(
    p: ActionProposal, s: AccountState, c: PolicyConfig, now: datetime
) -> list[RuleResult]:
    """R-PAYEE-ADD: adding a payee always needs MFA.

    A payee built from untrusted or model made data also carries a scam warning (urgency, a changed
    account, a request for secrecy). That is not enough on its own for a payment: R-PAYEE-COOLOFF
    caps what the new account can receive for 24 hours."""
    if p.action is not ActionType.ADD_PAYEE:
        return []
    out = [RuleResult("R-PAYEE-ADD", Decision.REQUIRE_MFA, "payee_add_mfa")]
    spec = p.new_payee
    if spec is None:
        out.append(RuleResult("R-PAYEE-ADD", Decision.DENY, "payee_add_incomplete"))
        return out
    if any(f.provenance in TAINTED for f in (spec.display_name, spec.region, spec.account_no)):
        out.append(RuleResult("R-PAYEE-ADD", Decision.REQUIRE_MFA, "payee_add_untrusted_warning"))
    return out


def r_payee_cooloff(
    p: ActionProposal, s: AccountState, c: PolicyConfig, now: datetime
) -> list[RuleResult]:
    """R-PAYEE-COOLOFF: the control for a user who has been deceived (grants and MFA cannot help).

    A deceived user confirms and enters a valid TOTP, so grants and MFA alone do not stop a payment
    to a scammer's account. A destination account first saved less than ``hours`` ago can receive at
    most the cap in total until ``hours`` after it was first saved. Above the cap the decision is
    DENY, with the earliest allowed time in the reason, whatever the grant or MFA. An account that
    came from UNTRUSTED or MODEL_INFERRED data (a document, a message, a model guess) gets the much
    lower ``untrusted_origin_cap_npr_minor`` (0 in the shipped config: a full hold, nothing can be
    paid to it for 24 hours); an account the user typed keeps the normal cap. After the hold, an
    untrusted origin account that was never verified is still untrusted: MFA with the warning
    and the normal cap in any 24 hours, until it is verified. The age and the
    running total belong to the destination ACCOUNT (hash), taken over every payee row with that
    hash, so renaming, re-adding or splitting a payment does not reset or multiply the cap."""
    if p.action not in PAYEE_ACTIONS or p.payee_id is None or p.amount is None:
        return []
    payee = s.payees.get(p.payee_id.value)
    if payee is None:
        return []  # R-OWNERSHIP denies an unknown payee
    key = payee.account_key
    no_key = not key  # an account with no hash cannot be totalled: fail closed below
    rows = [q for q in s.payees.values() if key and q.account_key == key] or [payee]
    first = min(q.created_at for q in rows)
    until = first + timedelta(hours=c.payee_cooloff.hours)
    untrusted = any(q.provenance in TAINTED for q in rows)
    # A payee the user typed (never untrusted) that a bank officer verified on a Confirmation
    # of Payee
    # match is no longer held: the cap exists for accounts nobody has checked (ADR 0018).
    # Verification
    # never releases an untrusted origin account early: its hold ends only with time (INV-4).
    verified_typed = not untrusted and not no_key and any(q.verified for q in rows)
    held = now < until and not verified_typed
    if not held and not (untrusted and not any(q.verified for q in rows)):
        return []
    # held: the hold applies. After it, an untrusted origin account nobody verified stays untrusted
    # (INV-4): MFA with the scam warning again, and at most the typed cap in any 24 hours.
    cap = (
        c.payee_cooloff.untrusted_origin_cap_npr_minor
        if untrusted and held
        else c.payee_cooloff.first_payment_cap_npr_minor
    )
    this = to_npr_minor(p.amount.value, s)
    earlier = [to_npr_minor(m, s) for m in s.paid_to_account.get(key, ())] if key else []
    over = no_key or this is None or any(e is None for e in earlier)
    if not over and this is not None:
        total = this + sum(e for e in earlier if e is not None)
        over = total > cap
    when = until.isoformat(timespec="minutes")
    if not held:
        if over:
            return [RuleResult("R-PAYEE-COOLOFF", Decision.DENY, f"unverified_cap@{when}|{cap}")]
        return [
            RuleResult("R-PAYEE-COOLOFF", Decision.REQUIRE_MFA, f"unverified_warning@{when}|{cap}")
        ]
    if not over:
        return []
    kind = "first_payment_cap_untrusted" if untrusted else "first_payment_cap"
    return [RuleResult("R-PAYEE-COOLOFF", Decision.DENY, f"{kind}@{when}|{cap}")]


def r_duplicate(
    p: ActionProposal, s: AccountState, c: PolicyConfig, now: datetime
) -> list[RuleResult]:
    """R-DUPLICATE: the same amount to the same account within 24 hours needs a fresh step-up.

    Two separate messages that ask for the same payment (a retry, a second message) are two
    proposals with two idempotency keys, so INV-6 does not see them as one. A payment of exactly
    the same amount and currency to the same destination ACCOUNT (hash, so a renamed payee does
    not hide it) that executed in the last 24 hours needs MFA, and the reason names the earlier
    payment (time and amount) so the user can see it is a repeat. It is a step-up, not a denial:
    a repeat can be genuine. It is not a high risk rule and never counts toward the safety lock."""
    if p.action is not ActionType.TRANSFER or p.payee_id is None or p.amount is None:
        return []
    payee = s.payees.get(p.payee_id.value)
    if payee is None or not payee.account_key:
        return []
    same = [
        i
        for i in s.paid_to_account_detail.get(payee.account_key, ())
        if i.amount == p.amount.value and now - timedelta(hours=24) <= i.at <= now
    ]
    if not same:
        return []
    last = max(same, key=lambda i: i.at)
    when = last.at.isoformat(timespec="minutes")
    key = f"same_payment@{when}|{last.amount.amount_minor}|{last.amount.currency.value}"
    return [RuleResult("R-DUPLICATE", Decision.REQUIRE_MFA, key)]


def r_tier(p: ActionProposal, s: AccountState, c: PolicyConfig, now: datetime) -> list[RuleResult]:
    """R-TIER-LOW and R-TIER-HIGH: the amount and payee tiers."""
    if p.action not in OUTGOING:
        if p.action is ActionType.ADD_PAYEE:
            return []
        if p.action is ActionType.SHARE_PASSPORT:
            return [RuleResult("R-TIER-HIGH", Decision.REQUIRE_MFA, "shares_personal_data")]
        return [RuleResult("R-TIER-LOW", Decision.REQUIRE_CONFIRM, "state_change")]
    out: list[RuleResult] = []
    npr = None if p.amount is None else to_npr_minor(p.amount.value, s)
    over_low = npr is None or npr > c.limits.low_npr_minor
    if over_low:
        out.append(RuleResult("R-TIER-HIGH", Decision.REQUIRE_MFA, "amount_above_low_limit"))
    if p.action is ActionType.SCHEDULE:
        out.append(RuleResult("R-TIER-HIGH", Decision.REQUIRE_MFA, "schedule"))
    payee = s.payees.get(p.payee_id.value) if p.payee_id is not None else None
    new_payee = False
    unverified = False
    if payee is not None:
        new_payee = now - payee.created_at < timedelta(days=c.new_payee_days)
        unverified = not payee.verified
        if new_payee:
            out.append(RuleResult("R-TIER-HIGH", Decision.REQUIRE_MFA, "new_payee"))
        if unverified:
            out.append(RuleResult("R-TIER-HIGH", Decision.REQUIRE_MFA, "unverified_payee"))
    tainted = (_payee_provenance(p, s) in TAINTED) or (_amount_provenance(p, s) in TAINTED)
    if not out and not tainted:
        out.append(RuleResult("R-TIER-LOW", Decision.REQUIRE_CONFIRM, "within_low_limit"))
    return out


def r_floor(p: ActionProposal, s: AccountState, c: PolicyConfig, now: datetime) -> list[RuleResult]:
    """R-FLOOR: no action is ever allowed without at least a user confirmation (INV-1, INV-10).

    Policy never returns ALLOW for an ``ActionProposal``; ALLOW exists only for read tools
    (``evaluate_read``). This rule makes that structural."""
    return [RuleResult("R-FLOOR", Decision.REQUIRE_CONFIRM, "confirmation_always")]


RULES: tuple[Rule, ...] = (
    r_lock,
    r_settings,
    r_ownership,
    r_invoice,
    r_contribution,
    r_passport,
    r_currency,
    r_balance,
    r_daily_limit,
    r_velocity,
    r_taint_payee,
    r_taint_amount,
    r_injection,
    r_risk,
    r_payee_add,
    r_payee_cooloff,
    r_duplicate,
    r_tier,
    r_floor,
)
