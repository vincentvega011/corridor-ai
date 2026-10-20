"""SQLAlchemy 2 models of the mock bank (docs/spec/modules.md 5.1).

Everything is synthetic. Money is always BIGINT minor units plus a currency column; FX rates are
NUMERIC(18,8). Database CHECK constraints are a second line of defence under the executor: a
negative balance, an unsupported currency, a non positive transfer amount or a duplicate
idempotency key (INV-6) is rejected by Postgres itself even if application code is wrong.

Account numbers are never stored: only an HMAC-SHA256 hash (``account_no_hash``) and the last 4
digits (``account_no_masked``). Geography uses ``region`` columns (ISO 3166 style codes, with Hong
Kong, Macao and Taiwan being regions of China if they ever appear). The ledger, grant and safety
lock tables are at the end of this file (phase P2).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from corridor.contracts.money import CurrencyCode

CURRENCIES = tuple(c.value for c in CurrencyCode)
_CCY_LIST = ", ".join(f"'{c}'" for c in CURRENCIES)
JsonType = JSON().with_variant(JSONB(), "postgresql")


def _ccy_check(column: str, name: str) -> CheckConstraint:
    return CheckConstraint(f"{column} IN ({_CCY_LIST})", name=name)


PROVENANCE_CHECK = (
    "provenance IN ('system', 'user_typed', 'user_confirmed', 'untrusted', 'model_inferred')"
)

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base with a stable naming convention for constraints."""

    metadata = MetaData(naming_convention=NAMING)


class User(Base):
    """A demo customer. ``home_region`` is the region of residence of the customer."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120))
    home_region: Mapped[str] = mapped_column(String(8))
    locale: Mapped[str] = mapped_column(String(16))
    risk_profile: Mapped[str] = mapped_column(String(16))
    mfa_seed_ref: Mapped[str] = mapped_column(String(200))  # path under secrets/, never the seed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("risk_profile IN ('low', 'medium', 'high')", name="risk_profile_valid"),
    )


class Account(Base):
    """A multi-currency account. Balance can never be negative (database level)."""

    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    balance_minor: Mapped[int] = mapped_column(BigInteger)
    account_no_masked: Mapped[str] = mapped_column(String(16))
    account_no_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16), server_default="spending")
    """``spending`` (the account payments normally leave from) or ``savings`` (a buffer, P3-1)."""

    __table_args__ = (
        _ccy_check("currency", "currency_supported"),
        CheckConstraint("kind IN ('spending', 'savings')", name="kind_valid"),
        CheckConstraint("balance_minor >= 0", name="balance_non_negative"),
        CheckConstraint("account_no_masked ~ '^\\*\\*\\*\\*[0-9]{4}$'", name="masked_format"),
        CheckConstraint("account_no_hash ~ '^[0-9a-f]{64}$'", name="hash_format"),
        CheckConstraint("status IN ('active', 'frozen', 'closed')", name="status_valid"),
        UniqueConstraint("user_id", "currency", "kind", name="uq_accounts_user_currency_kind"),
    )


class Payee(Base):
    """A saved beneficiary. ``account_no_masked`` is added to the spec so the UI can show the
    last 4 digits without ever holding the number."""

    __tablename__ = "payees"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    region: Mapped[str] = mapped_column(String(8))
    currency: Mapped[str] = mapped_column(String(3))
    provider_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    relation: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )  # mother, father, landlord ...
    account_no_masked: Mapped[str] = mapped_column(String(16))
    account_no_hash: Mapped[str] = mapped_column(String(64))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    added_via: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    provenance: Mapped[str] = mapped_column(String(16), server_default="system")
    """Provenance of display_name and the account number. A payee built from a document is
    ``untrusted`` and policy reads this column, never the request (INV-4)."""

    __table_args__ = (
        _ccy_check("currency", "currency_supported"),
        CheckConstraint(PROVENANCE_CHECK, name="provenance_valid"),
        CheckConstraint(
            "added_via IN ('settings', 'seed', 'agent_mfa', 'document')", name="added_via_valid"
        ),
        CheckConstraint("account_no_masked ~ '^\\*\\*\\*\\*[0-9]{4}$'", name="masked_format"),
        CheckConstraint("account_no_hash ~ '^[0-9a-f]{64}$'", name="hash_format"),
    )


class Provider(Base):
    """A fictional remittance provider on one corridor (source currency to destination currency)."""

    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    corridor_from: Mapped[str] = mapped_column(String(3))
    corridor_to: Mapped[str] = mapped_column(String(3))
    fee_fixed_minor: Mapped[int] = mapped_column(BigInteger)
    fee_pct_bps: Mapped[int] = mapped_column(Integer)
    fx_margin_bps: Mapped[int] = mapped_column(Integer)
    min_minor: Mapped[int] = mapped_column(BigInteger)
    max_minor: Mapped[int] = mapped_column(BigInteger)
    daily_limit_minor: Mapped[int] = mapped_column(BigInteger)
    eta_hours: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean)

    __table_args__ = (
        _ccy_check("corridor_from", "from_supported"),
        _ccy_check("corridor_to", "to_supported"),
        CheckConstraint("fee_fixed_minor >= 0", name="fee_fixed_non_negative"),
        CheckConstraint("fee_pct_bps >= 0 AND fx_margin_bps >= 0", name="bps_non_negative"),
        CheckConstraint("min_minor > 0 AND max_minor >= min_minor", name="limits_valid"),
        CheckConstraint("eta_hours >= 0", name="eta_non_negative"),
        Index("ix_providers_corridor", "corridor_from", "corridor_to"),
    )


class FxMidRate(Base):
    """A mid rate: ``rate`` units of the quote currency per 1 unit of the base currency."""

    __tablename__ = "fx_mid_rates"

    pair: Mapped[str] = mapped_column(String(7), primary_key=True)  # for example AUD/NPR
    as_of: Mapped[date] = mapped_column(Date, primary_key=True)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    source: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("rate > 0", name="rate_positive"),
        CheckConstraint("pair ~ '^[A-Z]{3}/[A-Z]{3}$'", name="pair_format"),
    )


class Invoice(Base):
    """An invoice owed by a user. The stored beneficiary is the trusted record (INV-4)."""

    __tablename__ = "invoices"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    issuer: Mapped[str] = mapped_column(String(120))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    due_date: Mapped[date] = mapped_column(Date)
    beneficiary_payee_id: Mapped[str] = mapped_column(ForeignKey("payees.id"))
    status: Mapped[str] = mapped_column(String(16))
    source_doc_hash: Mapped[str] = mapped_column(String(64))
    provenance: Mapped[str] = mapped_column(String(16), server_default="system")
    """Provenance of issuer and amount. Document derived invoices are ``untrusted``."""
    description: Mapped[str | None] = mapped_column(String(80), nullable=True)
    """What the invoice is for in words ("Semester 2, 2026"); shown instead of the id (P4-1)."""
    paid_minor: Mapped[int] = mapped_column(BigInteger, server_default="0")
    """Instalments paid so far (P3-1). ``status`` is ``paid`` only at ``amount_minor``."""

    __table_args__ = (
        _ccy_check("currency", "currency_supported"),
        CheckConstraint(PROVENANCE_CHECK, name="provenance_valid"),
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        CheckConstraint(
            "status IN ('open', 'part_paid', 'paid', 'cancelled')", name="status_valid"
        ),
        CheckConstraint("paid_minor >= 0 AND paid_minor <= amount_minor", name="paid_in_range"),
    )


class Transfer(Base):
    """An executed (or attempted) transfer. ``idempotency_key`` is UNIQUE (INV-6)."""

    __tablename__ = "transfers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    from_account: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    payee_id: Mapped[str] = mapped_column(ForeignKey("payees.id"))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    route_json: Mapped[dict[str, Any]] = mapped_column(JsonType)
    status: Mapped[str] = mapped_column(String(16))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    proposal_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        _ccy_check("currency", "currency_supported"),
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        CheckConstraint(
            "status IN ('pending', 'executed', 'failed', 'reversed')", name="status_valid"
        ),
        Index("ix_transfers_user_created", "user_id", "created_at"),
    )


class Schedule(Base):
    """A recurring transfer template (created only through policy checked actions)."""

    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    proposal_template_json: Mapped[dict[str, Any]] = mapped_column(JsonType)
    rrule: Mapped[str] = mapped_column(String(200))
    next_run: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16))

    __table_args__ = (
        CheckConstraint("status IN ('active', 'paused', 'cancelled')", name="status_valid"),
    )


class CreditEvent(Base):
    """A payment event feeding the Credit Passport (rent, tuition, remittance, bills)."""

    __tablename__ = "credit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    due_date: Mapped[date] = mapped_column(Date)
    paid_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    on_time: Mapped[bool] = mapped_column(Boolean)

    __table_args__ = (
        _ccy_check("currency", "currency_supported"),
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        CheckConstraint("kind IN ('rent', 'tuition', 'remittance', 'bill')", name="kind_valid"),
    )


class ConfirmationGrantRow(Base):
    """A server side confirmation grant (modules 5.8): one proposal hash, single use, 120 s."""

    __tablename__ = "confirmation_grants"

    grant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    proposal_hash: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    level: Mapped[str] = mapped_column(String(8))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, server_default="false")
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("level IN ('confirm', 'mfa')", name="level_valid"),
        CheckConstraint("expires_at = issued_at + interval '120 seconds'", name="ttl"),
        Index("ix_confirmation_grants_user", "user_id", "proposal_hash"),
    )


class SecurityEvent(Base):
    """A denied or failed high risk attempt, a failed TOTP or a critical event (INV-11 counters)."""

    __tablename__ = "security_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(16))
    ref: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "kind IN ('denied', 'failed_high_risk', 'totp_failed', 'critical', 'doc_denied')",
            name="kind_valid",
        ),
        Index("ix_security_events_user_ts", "user_id", "ts"),
        Index("ix_security_events_ref", "user_id", "kind", "ref"),
    )


class SafetyLock(Base):
    """Persisted safety lock state (INV-11). Unlocking needs MFA re-verification."""

    __tablename__ = "safety_locks"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    locked: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(String(32))
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    unlocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TotpStep(Base):
    """A TOTP time step already accepted for a user (a code can be used once)."""

    __tablename__ = "totp_steps"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    step: Mapped[int] = mapped_column(BigInteger, primary_key=True)


class LedgerEntryRow(Base):
    """The hash-chained audit ledger (modules 5.10). Written only through ``corridor.ledger``."""

    __tablename__ = "ledger_entries"

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    trace_id: Mapped[str] = mapped_column(String(128))
    actor: Mapped[str] = mapped_column(String(16))
    event_type: Mapped[str] = mapped_column(String(32))
    payload_canonical_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    policy_version_hash: Mapped[str] = mapped_column(String(64))
    prev_hash: Mapped[str] = mapped_column(String(64))
    entry_hash: Mapped[str] = mapped_column(String(64), unique=True)


class LedgerRootRow(Base):
    """An immutable Merkle root snapshot over a day or a session of ledger entries."""

    __tablename__ = "ledger_roots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(8))
    scope_key: Mapped[str] = mapped_column(String(128))
    first_seq: Mapped[int] = mapped_column(BigInteger)
    last_seq: Mapped[int] = mapped_column(BigInteger)
    entry_count: Mapped[int] = mapped_column(Integer)
    root_hash: Mapped[str] = mapped_column(String(64))
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_ledger_roots_scope", "scope", "scope_key"),)


class DocumentRow(Base):
    """A document processed by the quarantined reader. Always ``untrusted`` (INV-3, INV-4)."""

    __tablename__ = "documents"

    doc_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    injection_suspected: Mapped[bool] = mapped_column(Boolean)
    signals: Mapped[list[str]] = mapped_column(JsonType)
    fields: Mapped[dict[str, Any]] = mapped_column(JsonType)
    provenance: Mapped[str] = mapped_column(String(16), server_default="untrusted")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("kind IN ('invoice', 'message', 'statement')", name="kind_valid"),
        CheckConstraint("provenance = 'untrusted'", name="always_untrusted"),
        CheckConstraint("doc_hash ~ '^[0-9a-f]{64}$'", name="hash_format"),
        Index("ix_documents_user_flagged", "user_id", "injection_suspected"),
    )


class CopRegistry(Base):
    """Simulated Confirmation of Payee: the holder name registered for an account (synthetic data
    only).

    A bank officer can verify a payee only if the payee's name matches this holder name (ADR
    0012)."""

    __tablename__ = "cop_registry"

    account_no_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    holder_name: Mapped[str] = mapped_column(String(80))
    synthetic: Mapped[bool] = mapped_column(Boolean, server_default="true")

    __table_args__ = (
        CheckConstraint("account_no_hash ~ '^[0-9a-f]{64}$'", name="hash_format"),
        CheckConstraint("synthetic", name="always_synthetic"),
    )


class FamilyLink(Base):
    """A family member who may be asked to contribute to a user's payments (P3-1).

    The link is data set up by the bank (synthetic here), never by a model.
    ``per_request_limit_minor`` is the most one request may ask for (in the contributor's
    account currency), so the planner never needs to read the contributor's balance."""

    __tablename__ = "family_links"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    contributor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    contributor_account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    relation: Mapped[str] = mapped_column(String(32))
    per_request_limit_minor: Mapped[int] = mapped_column(BigInteger)

    __table_args__ = (
        CheckConstraint("per_request_limit_minor > 0", name="limit_positive"),
        CheckConstraint("user_id <> contributor_user_id", name="not_self"),
        UniqueConstraint("user_id", "contributor_user_id", name="uq_family_links_pair"),
    )


class ContributionRequest(Base):
    """A request for money from a family member. It moves no money: the contributor decides in their
    own account (INV-1: the requester can only ask)."""

    __tablename__ = "contribution_requests"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    requester_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    contributor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    target_account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    invoice_id: Mapped[str | None] = mapped_column(ForeignKey("invoices.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    proposal_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        _ccy_check("currency", "currency_supported"),
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        CheckConstraint("status IN ('pending', 'accepted', 'declined')", name="status_valid"),
    )


class Passport(Base):
    """An issued Credit Passport: the signed credential and the disclosures only the holder keeps.

    The disclosures hold the claim values and the random salts; they are the user's own data and are
    never sent to a model (INV-12). The credential itself is public (it only holds digests)."""

    __tablename__ = "passports"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token: Mapped[str] = mapped_column(Text)
    disclosures: Mapped[list[dict[str, Any]]] = mapped_column(JsonType)
    claim_ids: Mapped[list[str]] = mapped_column(JsonType)
    holder: Mapped[str] = mapped_column(String(40))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)


class PassportPresentation(Base):
    """One time a passport was shared: who it was for and which claims were disclosed."""

    __tablename__ = "passport_presentations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    passport_id: Mapped[str] = mapped_column(ForeignKey("passports.id"))
    audience: Mapped[str] = mapped_column(String(80))
    claim_ids: Mapped[list[str]] = mapped_column(JsonType)
    presentation: Mapped[dict[str, Any]] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)


PASSPORT_CHALLENGE_MINUTES = 10


class ParserTrace(Base):
    """The parser model's masked output for the officer trace (P3-4b). Not part of the hash
    chain: the ledger holds only ``ref_hash``; the row expires after ``RETENTION_DAYS``."""

    __tablename__ = "parser_traces"

    ref_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    masked_output: Mapped[str] = mapped_column(Text)


class PassportChallenge(Base):
    """A challenge nonce a verifier issued for one audience (ADR 0015). A presentation must
    answer it
    within ``PASSPORT_CHALLENGE_MINUTES`` and only once; expired rows are pruned."""

    __tablename__ = "passport_challenges"

    nonce: Mapped[str] = mapped_column(String(64), primary_key=True)
    audience: Mapped[str] = mapped_column(String(80))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    issued_by: Mapped[str] = mapped_column(
        String(16), server_default="verifier"
    )  # verifier or holder


class VerificationRequest(Base):
    """A customer's request that the bank verify one of their payees (ADR 0018). Only a bank officer
    resolves it, and only on a Confirmation of Payee match (``verify_payee``)."""

    __tablename__ = "verification_requests"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    payee_id: Mapped[str] = mapped_column(ForeignKey("payees.id"), index=True)
    status: Mapped[str] = mapped_column(String(16))  # pending, verified, refused
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    officer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    refusal: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'verified', 'refused')", name="status_valid"),
    )
