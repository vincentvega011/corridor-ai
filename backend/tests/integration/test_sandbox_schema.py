"""Schema level guarantees: Postgres itself rejects bad money, currencies and duplicate keys."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from corridor.sandbox.models import (
    Account,
    FxMidRate,
    Payee,
    Provider,
    Transfer,
)

NOW = datetime(2026, 10, 10, tzinfo=UTC)


@pytest.fixture
def session(pg_engine: Engine) -> Any:
    """A session whose work is always rolled back, so tests never change the seeded data."""
    s = Session(pg_engine)
    yield s
    s.rollback()
    s.close()


def _acct(**kw: Any) -> Account:
    base = dict(
        id="acc_x", user_id="usr_priya", currency="USD", balance_minor=100,
        account_no_masked="****0001", account_no_hash="a" * 64, status="active",
    )  # fmt: skip
    base.update(kw)
    return Account(**base)


def _transfer(**kw: Any) -> Transfer:
    base = dict(
        id="tr_x", user_id="usr_priya", from_account="acc_priya_aud", payee_id="pay_priya_landlord",
        amount_minor=100, currency="AUD", route_json={}, status="pending",
        idempotency_key="key-x", proposal_hash="b" * 64, created_at=NOW, executed_at=None,
    )  # fmt: skip
    base.update(kw)
    return Transfer(**base)


def test_negative_balance_rejected(session: Session) -> None:
    session.add(_acct(balance_minor=-1))
    with pytest.raises(IntegrityError, match="balance_non_negative"):
        session.flush()


def test_unsupported_currency_rejected_everywhere(session: Session) -> None:
    session.add(_acct(currency="EUR"))
    with pytest.raises(IntegrityError, match="currency_supported"):
        session.flush()
    session.rollback()
    session.add(_transfer(currency="BTC"))
    with pytest.raises(IntegrityError, match="currency_supported"):
        session.flush()
    session.rollback()
    session.add(
        Payee(
            id="pay_x", user_id="usr_priya", display_name="x", region="NP", currency="GBP",
            account_no_masked="****0001", account_no_hash="c" * 64, added_via="seed", created_at=NOW,
        )
    )  # fmt: skip
    with pytest.raises(IntegrityError, match="currency_supported"):
        session.flush()


def test_duplicate_idempotency_key_rejected(session: Session) -> None:
    session.add(_transfer(id="tr_1", idempotency_key="dup"))
    session.flush()
    session.add(_transfer(id="tr_2", idempotency_key="dup"))
    with pytest.raises(IntegrityError, match="uq_transfers_idempotency_key"):
        session.flush()


@pytest.mark.parametrize("amount", [0, -5])
def test_non_positive_transfer_rejected(session: Session, amount: int) -> None:
    session.add(_transfer(amount_minor=amount))
    with pytest.raises(IntegrityError, match="amount_positive"):
        session.flush()


def test_foreign_keys_and_status_checks(session: Session) -> None:
    session.add(_transfer(payee_id="pay_nobody"))
    with pytest.raises(IntegrityError, match="fk_transfers"):
        session.flush()
    session.rollback()
    session.add(_transfer(status="teleported"))
    with pytest.raises(IntegrityError, match="status_valid"):
        session.flush()


def test_one_account_per_currency_per_user(session: Session) -> None:
    session.add(_acct(id="acc_dup", currency="AUD"))
    with pytest.raises(IntegrityError, match="uq_accounts_user_currency"):
        session.flush()


def test_fx_rate_constraints(session: Session) -> None:
    session.add(FxMidRate(pair="AUD/NPR", as_of=date(2030, 1, 1), rate=Decimal("0"), source="t"))
    with pytest.raises(IntegrityError, match="rate_positive"):
        session.flush()
    session.rollback()
    session.add(FxMidRate(pair="aud-npr", as_of=date(2030, 1, 1), rate=Decimal("1"), source="t"))
    with pytest.raises((IntegrityError, DBAPIError)):
        session.flush()


def test_fx_rate_has_eight_decimal_places(session: Session) -> None:
    session.add(
        FxMidRate(pair="USD/AUD", as_of=date(2030, 1, 2), rate=Decimal("1.234567891"), source="t")
    )
    session.flush()
    stored = session.execute(
        text("SELECT rate FROM fx_mid_rates WHERE pair='USD/AUD' AND as_of='2030-01-02'")
    ).scalar_one()
    assert stored == Decimal("1.23456789")


def test_provider_limits_and_bps_checked(session: Session) -> None:
    base: dict[str, Any] = dict(
        id="prov_x", name="X", corridor_from="AUD", corridor_to="NPR", fee_fixed_minor=0,
        fee_pct_bps=0, fx_margin_bps=0, min_minor=100, max_minor=50, daily_limit_minor=100, eta_hours=1,
        active=True,
    )  # fmt: skip
    session.add(Provider(**base))
    with pytest.raises(IntegrityError, match="limits_valid"):
        session.flush()
    session.rollback()
    session.add(Provider(**{**base, "max_minor": 500, "fx_margin_bps": -1}))
    with pytest.raises(IntegrityError, match="bps_non_negative"):
        session.flush()


def test_schema_shape(pg_engine: Engine) -> None:
    insp = inspect(pg_engine)
    tables = set(insp.get_table_names()) - {"alembic_version"}
    assert tables == {
        "users", "accounts",
        "cop_registry", "payees", "providers", "fx_mid_rates", "invoices", "transfers",
        "schedules", "credit_events", "confirmation_grants", "security_events", "safety_locks",
        "totp_steps", "ledger_entries", "ledger_roots", "documents", "family_links",
        "contribution_requests", "passports", "passport_presentations", "passport_challenges", "verification_requests", "parser_traces",
    }  # fmt: skip
    for t in tables:
        cols = {c["name"] for c in insp.get_columns(t)}
        assert "country" not in cols  # geography uses `region`
        assert not {c for c in cols if "account_no" in c} - {"account_no_masked", "account_no_hash"}
    assert "region" in {c["name"] for c in insp.get_columns("payees")}
    assert "home_region" in {c["name"] for c in insp.get_columns("users")}
    types = {c["name"]: str(c["type"]) for c in insp.get_columns("accounts")}
    assert types["balance_minor"] == "BIGINT"
    assert (
        str({c["name"]: c["type"] for c in insp.get_columns("fx_mid_rates")}["rate"])
        == "NUMERIC(18, 8)"
    )


def test_account_numbers_are_never_stored(pg_engine: Engine) -> None:
    import json

    from corridor.sandbox.vault import ACCOUNTS_FILE

    numbers = json.loads((pg_engine.secrets_dir / ACCOUNTS_FILE).read_text())  # type: ignore[attr-defined]
    with pg_engine.connect() as conn:
        for table in ("accounts", "payees"):
            rows = conn.execute(
                text(f"SELECT account_no_masked, account_no_hash FROM {table}")
            ).all()
            for masked, h in rows:
                assert masked.startswith("****") and len(masked) == 8
                assert len(h) == 64
                assert all(n not in masked and n not in h for n in numbers.values())
        dump = " ".join(
            str(r)
            for t in ("users", "accounts", "payees", "invoices")
            for r in conn.execute(text(f"SELECT * FROM {t}"))
        )
        assert not [n for n in numbers.values() if n in dump]


def test_full_account_number_cannot_be_stored(session: Session) -> None:
    session.add(_acct(account_no_masked="912738032125"))
    with pytest.raises(IntegrityError, match="masked_format"):
        session.flush()
    session.rollback()
    session.add(_acct(account_no_hash="not-a-hash"))
    with pytest.raises(IntegrityError, match="hash_format"):
        session.flush()
