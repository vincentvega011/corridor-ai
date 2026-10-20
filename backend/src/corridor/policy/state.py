"""AccountState: the deterministic snapshot the policy engine decides on (docs/spec/modules.md 5.7).

It is built from the sandbox by ``corridor.sandbox.account_state`` and contains everything a rule
may look at: balances, payees with ages and provenance, today's outgoing totals, recent transfer
times, the lock flag, the FX rates used, stored invoices and flagged documents. The engine never
queries anything itself (INV-2).
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from corridor.contracts.money import CurrencyCode, Money
from corridor.contracts.provenance import Provenance


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AccountInfo(_Frozen):
    """One account of the user."""

    currency: CurrencyCode
    balance_minor: int
    active: bool = True


class PayeeInfo(_Frozen):
    """One saved payee of the user, with the provenance stored in the database."""

    currency: CurrencyCode
    verified: bool
    created_at: datetime
    provenance: Provenance
    cop: str = "unknown"
    """Simulated Confirmation of Payee: match, mismatch, or unknown (no registry record)."""
    account_key: str = ""
    """Hash of the destination account number. Payees with the same key are the same destination,
    whatever their name or id (R-PAYEE-COOLOFF cannot be dodged by renaming or re-adding)."""


class PaidItem(_Frozen):
    """One executed payment to a destination account: when and how much (R-DUPLICATE)."""

    at: datetime
    amount: Money


class InvoiceInfo(_Frozen):
    """One open invoice: the stored beneficiary is the trusted record (INV-4)."""

    beneficiary_payee_id: str
    amount_minor: int
    currency: CurrencyCode
    provenance: Provenance
    paid_minor: int = 0
    """Instalments already paid (P3-1). The outstanding amount is ``amount_minor - paid_minor``."""


class FamilyLinkInfo(_Frozen):
    """A family member this user may ask for money, and the most one request may ask for."""

    currency: CurrencyCode
    per_request_limit_minor: int
    requests_last_24h: int = 0
    """Requests this user already made to this family member in the last rolling 24 hours."""


class PassportInfo(_Frozen):
    """An issued passport of this user: the claims it can disclose and when it expires."""

    claim_ids: frozenset[str]
    expires_at: datetime


class AccountState(_Frozen):
    """Everything policy may know about the user at one instant."""

    user_id: str
    accounts: dict[str, AccountInfo]
    payees: dict[str, PayeeInfo]
    invoices: dict[str, InvoiceInfo]
    fx_to_npr: dict[CurrencyCode, Decimal]
    """NPR per one unit of the currency (NPR itself is 1). The snapshot used for every limit."""
    outgoing_today: tuple[Money, ...] = ()
    """Executed outgoing amounts in the last 24 hours (rolling, not a calendar day)."""
    paid_to_account: dict[str, tuple[Money, ...]] = {}
    """Executed amounts in the last 24 hours by destination account key (R-PAYEE-COOLOFF)."""
    paid_to_account_detail: dict[str, tuple[PaidItem, ...]] = {}
    """The same payments as ``paid_to_account`` with their times, newest last (R-DUPLICATE)."""
    recent_transfer_times: tuple[datetime, ...] = ()
    locked: bool = False
    flagged_docs: frozenset[str] = frozenset()
    supported_pairs: frozenset[str] = frozenset()
    """``FROM/TO`` currency pairs that have an active remittance corridor (payments to payees)."""
    passports: dict[str, PassportInfo] = {}
    """Passport id to its claims and expiry (R-PASSPORT)."""
    credit_event_count: int = 0
    timezone: str = "UTC"
    """IANA zone of the user's region, for the unusual hour reason (R-RISK)."""
    history_median_npr_minor: int = 0
    """Median executed payment of the last 6 months in NPR minor units; 0 when under 3 payments."""
    recent_denials: int = 0
    """Denied or failed high risk attempts in the safety lock window (R-RISK)."""
    """Payment history events on file; a passport cannot be issued without any (R-PASSPORT)."""
    family_links: dict[str, FamilyLinkInfo] = {}
    """Contributor user id to the link, for REQUEST_CONTRIBUTION (R-CONTRIBUTION)."""
    convert_pairs: frozenset[str] = frozenset()
    """``FROM/TO`` currency pairs with an FX rate (conversion between the user's own accounts)."""
