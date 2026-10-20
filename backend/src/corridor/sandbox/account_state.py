"""Build the policy engine's ``AccountState`` from the sandbox (docs/spec/modules.md 5.7).

This is the only place that turns database rows into the snapshot policy decides on. Provenance of
payees and invoices comes from their database columns, never from a request or a model (INV-4). It
is used twice: when the agent proposes, and again inside the executor's transaction so the decision
is re-evaluated with fresh state at execution time (time of check equals time of use).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from corridor.contracts.money import CurrencyCode, Money
from corridor.contracts.provenance import Provenance
from corridor.policy.config import PolicyConfig
from corridor.policy.state import (
    AccountInfo,
    AccountState,
    FamilyLinkInfo,
    InvoiceInfo,
    PaidItem,
    PassportInfo,
    PayeeInfo,
)
from corridor.sandbox.cop import names_match
from corridor.sandbox.models import (
    Account,
    ContributionRequest,
    CopRegistry,
    CreditEvent,
    DocumentRow,
    FamilyLink,
    Invoice,
    Passport,
    Payee,
    Provider,
    SafetyLock,
    SecurityEvent,
    Transfer,
    User,
)
from corridor.sandbox.read_tools import _rate_between


def _fx_to_npr(session: Session) -> dict[CurrencyCode, Decimal]:
    rates: dict[CurrencyCode, Decimal] = {CurrencyCode.NPR: Decimal(1)}
    for ccy in CurrencyCode:
        if ccy is CurrencyCode.NPR:
            continue
        found = _rate_between(session, ccy.value, "NPR")
        if found is not None:
            rates[ccy] = found[0]
    return rates


def _convert_pairs(session: Session) -> frozenset[str]:
    pairs: set[str] = set()
    for a in CurrencyCode:
        for b in CurrencyCode:
            if a is not b and _rate_between(session, a.value, b.value) is not None:
                pairs.add(f"{a.value}/{b.value}")
    return frozenset(pairs)


def _timezone_of(region: str | None) -> str:
    """The IANA zone of a region of residence (``config/corridors.yaml``), UTC when unknown."""
    from functools import lru_cache

    @lru_cache(maxsize=1)
    def table() -> dict[str, str]:
        import yaml

        path = Path(__file__).resolve().parents[3] / "config" / "corridors.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in (raw.get("region_timezone") or {}).items()}

    return table().get(region or "", "UTC")


def build_account_state(
    session: Session,
    user_id: str,
    now: datetime,
    config: PolicyConfig,
) -> AccountState:
    """A deterministic snapshot of ``user_id`` at ``now`` (UTC, timezone aware).

    Documents the reader flagged as ``injection_suspected`` are read from the database here, never
    from the proposal, so R-INJECTION sees them at proposal time and again at execution time."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone aware")
    now = now.astimezone(UTC)
    accounts = {
        a.id: AccountInfo(
            currency=CurrencyCode(a.currency),
            balance_minor=a.balance_minor,
            active=a.status == "active",
        )
        for a in session.scalars(select(Account).where(Account.user_id == user_id))
    }
    holders = dict(
        session.execute(select(CopRegistry.account_no_hash, CopRegistry.holder_name)).all()
    )
    payees = {
        p.id: PayeeInfo(
            currency=CurrencyCode(p.currency),
            verified=p.verified_at is not None,
            created_at=p.created_at,
            provenance=Provenance(p.provenance),
            account_key=p.account_no_hash,
            cop=(
                "unknown"
                if p.account_no_hash not in holders
                else "match"
                if names_match(p.display_name, holders[p.account_no_hash])
                else "mismatch"
            ),
        )
        for p in session.scalars(select(Payee).where(Payee.user_id == user_id))
    }
    invoices = {
        i.id: InvoiceInfo(
            beneficiary_payee_id=i.beneficiary_payee_id,
            amount_minor=i.amount_minor,
            currency=CurrencyCode(i.currency),
            provenance=Provenance(i.provenance),
            paid_minor=i.paid_minor,
        )
        for i in session.scalars(
            select(Invoice).where(
                Invoice.user_id == user_id, Invoice.status.in_(("open", "part_paid"))
            )
        )
    }
    family_links = {
        fl.contributor_user_id: FamilyLinkInfo(
            currency=CurrencyCode(
                session.get(Account, fl.contributor_account_id).currency  # type: ignore[union-attr]
            ),
            per_request_limit_minor=fl.per_request_limit_minor,
            requests_last_24h=int(
                session.scalar(
                    select(func.count())
                    .select_from(ContributionRequest)
                    .where(
                        ContributionRequest.requester_user_id == user_id,
                        ContributionRequest.contributor_user_id == fl.contributor_user_id,
                        ContributionRequest.created_at > now - timedelta(hours=24),
                    )
                )
                or 0
            ),
        )
        for fl in session.scalars(select(FamilyLink).where(FamilyLink.user_id == user_id))
    }
    passports = {
        pp.id: PassportInfo(claim_ids=frozenset(pp.claim_ids), expires_at=pp.expires_at)
        for pp in session.scalars(select(Passport).where(Passport.user_id == user_id))
    }
    fx_npr = _fx_to_npr(session)
    credit_events = session.scalar(
        select(func.count()).select_from(CreditEvent).where(CreditEvent.user_id == user_id)
    )
    day_start = now - timedelta(hours=24)  # a rolling day: no reset at midnight to spend twice
    window_start = now - timedelta(minutes=config.velocity.window_minutes)
    transfers = session.scalars(
        select(Transfer).where(
            Transfer.user_id == user_id,
            Transfer.status == "executed",
            Transfer.created_at >= min(day_start, window_start),
            Transfer.created_at <= now,
        )
    ).all()
    outgoing_today = tuple(
        Money(amount_minor=t.amount_minor, currency=CurrencyCode(t.currency))
        for t in transfers
        if t.created_at >= day_start
    )
    recent = tuple(sorted(t.created_at for t in transfers if t.created_at >= window_start))
    key_of = {pid: info.account_key for pid, info in payees.items()}
    paid_to: dict[str, list[Money]] = {}
    paid_detail: dict[str, list[PaidItem]] = {}
    for (
        t
    ) in transfers:  # grouped by the destination ACCOUNT, so a renamed or re-added payee shares it
        if t.created_at >= day_start and t.payee_id in key_of:
            paid = Money(amount_minor=t.amount_minor, currency=CurrencyCode(t.currency))
            paid_to.setdefault(key_of[t.payee_id], []).append(paid)
            paid_detail.setdefault(key_of[t.payee_id], []).append(
                PaidItem(at=t.created_at, amount=paid)
            )
    since = now - timedelta(days=183)
    past = sorted(
        int(rate * t.amount_minor)
        for t in session.scalars(
            select(Transfer).where(
                Transfer.user_id == user_id,
                Transfer.status == "executed",
                Transfer.created_at >= since,
            )
        )
        if (rate := fx_npr.get(CurrencyCode(t.currency))) is not None
    )
    median = past[len(past) // 2] if len(past) >= 3 else 0
    denials = session.scalar(
        select(func.count())
        .select_from(SecurityEvent)
        .where(
            SecurityEvent.user_id == user_id,
            SecurityEvent.kind.in_(("denied", "failed_high_risk", "doc_denied")),
            SecurityEvent.ts >= now - timedelta(minutes=config.lock.window_minutes),
        )
    )
    user = session.get(User, user_id)
    lock = session.get(SafetyLock, user_id)
    pairs = frozenset(
        f"{p.corridor_from}/{p.corridor_to}"
        for p in session.scalars(select(Provider).where(Provider.active.is_(True)))
    )
    return AccountState(
        user_id=user_id,
        accounts=accounts,
        payees=payees,
        invoices=invoices,
        family_links=family_links,
        passports=passports,
        credit_event_count=int(credit_events or 0),
        timezone=_timezone_of(user.home_region if user else None),
        history_median_npr_minor=median,
        recent_denials=int(denials or 0),
        fx_to_npr=fx_npr,
        outgoing_today=outgoing_today,
        paid_to_account={k: tuple(v) for k, v in paid_to.items()},
        paid_to_account_detail={
            k: tuple(sorted(v, key=lambda i: i.at)) for k, v in paid_detail.items()
        },
        recent_transfer_times=recent,
        locked=bool(lock and lock.locked),
        flagged_docs=frozenset(
            session.scalars(
                select(DocumentRow.doc_hash).where(
                    DocumentRow.user_id == user_id, DocumentRow.injection_suspected.is_(True)
                )
            )
        ),
        supported_pairs=pairs,
        convert_pairs=_convert_pairs(session),
    )
