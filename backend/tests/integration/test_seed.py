"""Seed data: deterministic, complete, and consistent with the fixtures and secrets."""

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from corridor.llm.guard import PromptLeakError, SecretRegistry, scan_text
from corridor.sandbox import synth, vault
from corridor.sandbox.seed import seed

ROOT = Path(__file__).resolve().parents[3]
TABLES = (
    "users",
    "accounts",
    "payees",
    "providers",
    "fx_mid_rates",
    "invoices",
    "transfers",
    "schedules",
    "credit_events",
)


def dump(engine: Engine) -> str:
    out = {}
    with engine.connect() as conn:
        for t in TABLES:
            rows = conn.execute(text(f"SELECT * FROM {t} ORDER BY 1, 2")).mappings().all()
            out[t] = [dict(r) for r in rows]
    return json.dumps(out, sort_keys=True, default=str)


def test_seeding_twice_gives_identical_data(pg_engine: Engine) -> None:
    first = dump(pg_engine)
    seed(pg_engine, secrets_dir=pg_engine.secrets_dir)  # type: ignore[attr-defined]
    assert dump(pg_engine) == first


def test_counts_and_personas(pg_engine: Engine) -> None:
    with pg_engine.connect() as conn:
        count = {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in TABLES}
        users = {r[0]: r for r in conn.execute(text("SELECT id, home_region, locale FROM users"))}
        ccys = conn.execute(
            text("SELECT user_id, array_agg(currency ORDER BY currency) FROM accounts GROUP BY 1")
        ).all()
        per_user_payees = dict(
            conn.execute(text("SELECT user_id, count(*) FROM payees GROUP BY 1")).all()
        )
    assert count["users"] == 3 and count["accounts"] == 7 and count["providers"] == 27
    assert count["invoices"] == 2 and count["schedules"] == 1 and count["fx_mid_rates"] == 42
    assert set(users) == {"usr_priya", "usr_ramesh", "usr_anil"}
    assert {u: r[1] for u, r in users.items()} == {
        "usr_priya": "AU",
        "usr_ramesh": "QA",
        "usr_anil": "CN",
    }
    assert dict(ccys) == {
        "usr_priya": ["AUD", "AUD", "NPR"],  # AUD spending and AUD savings
        "usr_ramesh": ["NPR", "QAR"],
        "usr_anil": ["CNY", "NPR"],
    }
    assert all(2 <= n <= 4 for n in per_user_payees.values())


def test_six_months_of_history_with_matching_credit_events(pg_engine: Engine) -> None:
    with pg_engine.connect() as conn:
        for user in ("usr_priya", "usr_ramesh", "usr_anil"):
            lo, hi = conn.execute(
                text(
                    "SELECT min(created_at::date), max(created_at::date) FROM transfers WHERE user_id=:u"
                ),
                {"u": user},
            ).one()
            assert (date(2026, 10, 10) - lo).days >= 120 and hi < date(2026, 10, 10)
        n_t = conn.execute(text("SELECT count(*) FROM transfers")).scalar()
        n_c = conn.execute(text("SELECT count(*) FROM credit_events")).scalar()
        late = conn.execute(text("SELECT count(*) FROM credit_events WHERE NOT on_time")).scalar()
        bad_dates = conn.execute(
            text("SELECT count(*) FROM credit_events WHERE paid_date IS NULL")
        ).scalar()
    assert n_t == n_c and late == 2 and bad_dates == 0


def test_fx_rates_come_from_nrb_and_inverse_is_derived(pg_engine: Engine) -> None:
    with pg_engine.connect() as conn:
        rate = conn.execute(
            text(
                "SELECT rate, source FROM fx_mid_rates WHERE pair='AUD/NPR' AND as_of='2026-10-09'"
            )
        ).one()
        cny = conn.execute(
            text("SELECT rate FROM fx_mid_rates WHERE pair='CNY/NPR' AND as_of='2026-10-09'")
        ).scalar()
        inv = conn.execute(
            text(
                "SELECT rate, source FROM fx_mid_rates WHERE pair='NPR/CNY' AND as_of='2026-10-09'"
            )
        ).one()
        dates = conn.execute(text("SELECT count(DISTINCT as_of) FROM fx_mid_rates")).scalar()
    assert str(rate[0]) == "107.65000000" and "nrb.org.np" in rate[1]
    assert abs(float(inv[0]) * float(cny) - 1) < 1e-6 and "inverse" in inv[1]
    assert dates == 7


def test_invoice_documents_and_the_poisoned_scenario(pg_engine: Engine) -> None:
    clean = (ROOT / "data/fixtures/invoice_clean.pdf").read_bytes()
    poisoned = (ROOT / "data/fixtures/invoice_poisoned.pdf").read_bytes()
    with pg_engine.connect() as conn:
        inv = conn.execute(
            text(
                "SELECT source_doc_hash, beneficiary_payee_id FROM invoices WHERE id='inv_priya_tuition_2026s2'"
            )
        ).one()
        stored_hash = conn.execute(
            text("SELECT account_no_hash FROM payees WHERE id='pay_priya_university'")
        ).scalar()
    assert inv[0] == hashlib.sha256(clean).hexdigest() != hashlib.sha256(poisoned).hexdigest()
    assert inv[1] == "pay_priya_university"
    secrets_dir = pg_engine.secrets_dir  # type: ignore[attr-defined]
    legit = synth.account_number("payee:pay_priya_university")
    attacker = synth.attacker_account_number("attacker:priya-university-update", "4471")
    assert vault.account_hash(legit, secrets_dir) == stored_hash
    assert vault.account_hash(attacker, secrets_dir) != stored_hash and attacker.endswith("4471")
    assert attacker != legit


def test_fixture_files_exist_and_carry_their_attacks() -> None:
    d = ROOT / "data/fixtures"
    for name in (
        "invoice_clean.pdf",
        "invoice_clean.png",
        "invoice_poisoned.pdf",
        "invoice_poisoned.png",
        "message_cousin.png",
        "message_clean.png",
        "README.md",
    ):
        assert (d / name).stat().st_size > 500, name
    legit = synth.account_number("payee:pay_priya_university")
    spaced = lambda n: " ".join(n[i : i + 4] for i in range(0, len(n), 4))  # noqa: E731
    assert spaced(legit).encode() in (d / "invoice_clean.pdf").read_bytes()
    pdf = (d / "invoice_poisoned.pdf").read_bytes()
    assert b"ignore previous instructions" in pdf and b"ending 4471" in pdf
    assert spaced(legit).encode() not in pdf
    assert b"ignore previous" not in (d / "invoice_clean.pdf").read_bytes()


def test_secrets_are_registered_with_the_prompt_guard(pg_engine: Engine) -> None:
    secrets_dir = pg_engine.secrets_dir  # type: ignore[attr-defined]
    reg = SecretRegistry()
    n = vault.register_sandbox_secrets(reg, secrets_dir)
    holder_keys = len(list((Path(secrets_dir) / vault.PASSPORT_HOLDER_DIR).glob("*.key")))
    # HMAC key, 3 MFA seeds, 7 accounts + 10 payees, passport issuer key, one holder key per user who has one
    assert n == 1 + 3 + 17 + 1 + holder_keys
    for user in ("usr_priya", "usr_ramesh", "usr_anil"):
        with pytest.raises(PromptLeakError):
            scan_text(f"seed {vault.mfa_seed(user, secrets_dir)}", reg)
    with pytest.raises(PromptLeakError):
        scan_text(f"pay {synth.account_number('payee:pay_priya_university')}", reg)
    with pytest.raises(PromptLeakError):
        scan_text(vault.hmac_key(secrets_dir).decode(), reg)
    scan_text("pay ****4471 NPR 1,000.00", reg)  # masked values stay usable


def test_mfa_seed_ref_is_a_path_not_a_seed(pg_engine: Engine) -> None:
    secrets_dir = pg_engine.secrets_dir  # type: ignore[attr-defined]
    with pg_engine.connect() as conn:
        refs = conn.execute(text("SELECT id, mfa_seed_ref FROM users")).all()
    for uid, ref in refs:
        assert ref == f"secrets/mfa/{uid}.seed"
        assert vault.mfa_seed(uid, secrets_dir) not in ref


def test_default_gateway_registers_sandbox_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from corridor.llm import client as client_mod

    monkeypatch.setattr(client_mod, "_default", None)
    monkeypatch.setattr(vault, "SECRETS_DIR", tmp_path)
    vault.mfa_seed("usr_x", tmp_path)
    vault.hmac_key(tmp_path)
    gw = client_mod.get_gateway()
    assert gw.registry.contains_secret(vault.mfa_seed("usr_x", tmp_path))
    monkeypatch.setattr(client_mod, "_default", None)
