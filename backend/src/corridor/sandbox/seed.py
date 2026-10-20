"""Deterministic seeding of the mock bank (``make seed``).

Everything comes from ``data/seeds/*.yaml``, ``data/fx/nrb_rates.json`` and a fixed RNG seed, with
no wall clock: running the seed twice produces identical rows. Only the HMAC account hashes depend
on the secret key, which is generated once and then persisted under ``secrets/``.
"""

import hashlib
import json
import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import Engine, delete, text
from sqlalchemy.orm import Session

from corridor.config import REPO_ROOT
from corridor.contracts.money import RATE_QUANTUM
from corridor.sandbox import synth, vault
from corridor.sandbox.models import (
    Account,
    ContributionRequest,
    CopRegistry,
    CreditEvent,
    FamilyLink,
    FxMidRate,
    Invoice,
    ParserTrace,
    Passport,
    PassportChallenge,
    PassportPresentation,
    Payee,
    Provider,
    Schedule,
    Transfer,
    User,
    VerificationRequest,
)

DATA_DIR = REPO_ROOT / "data"
NRB_URL = "https://www.nrb.org.np/api/forex/v1/rates"


@dataclass(frozen=True)
class SeedSummary:
    """Row counts written by a seed run."""

    users: int
    accounts: int
    payees: int
    providers: int
    fx_rates: int
    transfers: int
    credit_events: int
    invoices: int
    schedules: int
    as_of: date


def _yaml(name: str, data_dir: Path) -> dict[str, Any]:
    loaded = yaml.safe_load((data_dir / "seeds" / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _at(d: date, hour: int = 10) -> datetime:
    return datetime(d.year, d.month, d.day, hour, 0, tzinfo=UTC)


def _fx_rows(data_dir: Path) -> list[FxMidRate]:
    """Mid rates from NRB buy/sell: (buy + sell) / 2 / unit, NPR per 1 foreign unit.

    NPR/CNY is derived as the inverse of CNY/NPR (no direct NRB quote for NPR to foreign)."""
    raw = json.loads((data_dir / "fx" / "nrb_rates.json").read_text(encoding="utf-8"))
    rows: list[FxMidRate] = []
    for day in raw["days"]:
        d = date.fromisoformat(day["date"])
        for ccy, r in sorted(day["rates"].items()):
            mid = (Decimal(r["buy"]) + Decimal(r["sell"])) / 2 / Decimal(r["unit"])
            mid = mid.quantize(RATE_QUANTUM, rounding=ROUND_HALF_EVEN)
            src = f"NRB published buy/sell mid, {NRB_URL}, rate date {d}, retrieved {raw['retrieved']}"
            rows.append(FxMidRate(pair=f"{ccy}/NPR", as_of=d, rate=mid, source=src))
            if ccy == "CNY":
                inv = (Decimal(1) / mid).quantize(RATE_QUANTUM, rounding=ROUND_HALF_EVEN)
                rows.append(
                    FxMidRate(
                        pair="NPR/CNY", as_of=d, rate=inv, source=src + " (inverse of CNY/NPR)"
                    )
                )
    return rows


def _rate_on(rows: list[FxMidRate], pair: str, d: date) -> Decimal:
    cands = sorted((r for r in rows if r.pair == pair and r.as_of <= d), key=lambda r: r.as_of)
    return (
        cands[-1] if cands else min((r for r in rows if r.pair == pair), key=lambda r: r.as_of)
    ).rate


def _months(start: date, end: date) -> list[tuple[int, int]]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _due_dates(pattern: dict[str, Any], start: date, end: date) -> list[date]:
    if "dates" in pattern:
        parsed = (date.fromisoformat(str(x)) for x in pattern["dates"])
        return sorted(d for d in parsed if start <= d < end)
    return sorted(
        d for y, m in _months(start, end) if start <= (d := date(y, m, pattern["day"])) < end
    )


def seed(
    engine: Engine, data_dir: Path | None = None, secrets_dir: Path | None = None
) -> SeedSummary:
    """Wipe the sandbox tables and insert the deterministic seed data."""
    data_dir = data_dir or DATA_DIR
    users_cfg = _yaml("users.yaml", data_dir)
    providers_cfg = _yaml("providers.yaml", data_dir)
    as_of: date = users_cfg["as_of"]
    rng = random.Random(users_cfg["rng_seed"])
    history_start = as_of - timedelta(days=183)
    fx = _fx_rows(data_dir)

    numbers: dict[str, str] = {}

    def number(key: str) -> str:
        numbers[key] = synth.account_number(key)
        return numbers[key]

    counts = dict(accounts=0, payees=0, transfers=0, credit_events=0)
    with Session(engine) as s, s.begin():
        # reset the safety tables too (dev and test only; TRUNCATE is an owner level admin action)
        for t in ("ledger_entries", "ledger_roots"):
            s.execute(text(f"ALTER TABLE {t} DISABLE TRIGGER {t}_no_truncate"))
        s.execute(
            text(
                "TRUNCATE ledger_entries, ledger_roots, confirmation_grants, security_events, "
                "safety_locks, totp_steps, documents"
            )
        )
        for t in ("ledger_entries", "ledger_roots"):
            s.execute(text(f"ALTER TABLE {t} ENABLE TRIGGER {t}_no_truncate"))
        for model in (
            ParserTrace,
            PassportPresentation,
            PassportChallenge,
            VerificationRequest,
            Passport,
            ContributionRequest,
            FamilyLink,
            Transfer,
            Schedule,
            CreditEvent,
            Invoice,
            Payee,
            CopRegistry,
            Account,
            Provider,
            FxMidRate,
            User,
        ):
            s.execute(delete(model))

        for p in providers_cfg["providers"]:
            s.add(
                Provider(
                    id=p["id"], name=p["name"], corridor_from=p["from"], corridor_to=p["to"],
                    fee_fixed_minor=p["fixed"], fee_pct_bps=p["pct_bps"], fx_margin_bps=p["margin_bps"],
                    min_minor=p["min"], max_minor=p["max"], daily_limit_minor=p["daily"], eta_hours=p["eta_hours"], active=p["active"],
                )
            )  # fmt: skip
        s.add_all(fx)
        providers = {p["id"]: p for p in providers_cfg["providers"]}
        payee_ccy: dict[str, str] = {}

        for u in users_cfg["users"]:
            created = _at(as_of - timedelta(days=400), 9)
            s.add(
                User(
                    id=u["id"], display_name=u["display_name"], home_region=u["home_region"],
                    locale=u["locale"], risk_profile=u["risk_profile"],
                    mfa_seed_ref=vault.mfa_seed_ref(u["id"]), created_at=created,
                )
            )  # fmt: skip
            vault.mfa_seed(u["id"], secrets_dir)
            s.flush()
            for a in u["accounts"]:
                num = number(f"acct:{a['id']}")
                s.add(
                    Account(
                        id=a["id"], user_id=u["id"], currency=a["currency"],
                        balance_minor=a["balance_minor"], account_no_masked=synth.masked(num),
                        account_no_hash=vault.account_hash(num, secrets_dir), status="active",
                        kind=a.get("kind", "spending"),
                    )
                )  # fmt: skip
                counts["accounts"] += 1
            for p in u["payees"]:
                num = number(f"payee:{p['id']}")
                added = _at(as_of - timedelta(days=p["added_days_ago"]), 9)
                s.add(
                    Payee(
                        id=p["id"], user_id=u["id"], display_name=p["name"], region=p["region"],
                        currency=p["currency"], provider_ref=p["provider_ref"], relation=p.get("relation"),
                        account_no_masked=synth.masked(num),
                        account_no_hash=vault.account_hash(num, secrets_dir),
                        verified_at=added + timedelta(days=1), added_via="seed", created_at=added,
                    )
                )  # fmt: skip
                payee_ccy[p["id"]] = p["currency"]
                counts["payees"] += 1
            s.flush()

            for pat in sorted(u["patterns"], key=lambda x: (x["kind"], x["payee"])):
                for due in _due_dates(pat, history_start, as_of):
                    jit = pat.get("jitter_pct", 0)
                    delta = rng.randint(-jit * 100, jit * 100) if jit else 0
                    amount = pat["amount_minor"] + round(pat["amount_minor"] * delta / 10000)
                    late = f"{due:%Y-%m}" in pat.get("late_months", [])
                    paid = (
                        due + timedelta(days=rng.randint(3, 6))
                        if late
                        else due - timedelta(days=rng.randint(0, 2))
                    )
                    tid = (
                        f"tr_{u['id'][4:]}_{pat['kind']}_{pat['payee'].split('_')[-1]}_{due:%Y%m%d}"
                    )
                    route: dict[str, Any] = {"type": "domestic"}
                    if pat["kind"] == "remittance":
                        prov = providers[pat["provider"]]
                        rate = _rate_on(fx, f"{prov['from']}/{prov['to']}", due)
                        fee = prov["fixed"] + (amount * prov["pct_bps"] + 5000) // 10000
                        net_rate = rate * (Decimal(10000 - prov["margin_bps"]) / Decimal(10000))
                        delivered = int(
                            (Decimal(amount - prov["fixed"]) * net_rate).to_integral_value(
                                ROUND_HALF_EVEN
                            )
                        )
                        route = {
                            "type": "remittance", "provider": prov["id"], "fee_minor": fee,
                            "mid_rate": str(rate), "delivered_minor": delivered, "delivered_currency": prov["to"],
                        }  # fmt: skip
                    s.add(
                        Transfer(
                            id=tid, user_id=u["id"], from_account=pat["account"], payee_id=pat["payee"],
                            amount_minor=amount, currency=pat["currency"], route_json=route,
                            status="executed", idempotency_key=f"seed-{tid}",
                            proposal_hash=hashlib.sha256(f"seed:{tid}".encode()).hexdigest(),
                            created_at=_at(paid, 9), executed_at=_at(paid, 10),
                        )
                    )  # fmt: skip
                    s.add(
                        CreditEvent(
                            id=f"ce_{tid[3:]}", user_id=u["id"], kind=pat["kind"], amount_minor=amount,
                            currency=pat["currency"], due_date=due, paid_date=paid, on_time=not late,
                        )
                    )  # fmt: skip
                    counts["transfers"] += 1
                    counts["credit_events"] += 1
            s.flush()

        for fl in users_cfg.get("family_links", []):
            s.add(
                FamilyLink(
                    id=fl["id"], user_id=fl["user"], contributor_user_id=fl["contributor"],
                    contributor_account_id=fl["contributor_account"], relation=fl["relation"],
                    per_request_limit_minor=fl["per_request_limit_minor"],
                )
            )  # fmt: skip
        s.flush()

        n_inv = 0
        for inv in users_cfg["invoices"]:
            doc = inv.get("document")
            path = REPO_ROOT / doc if doc else None
            if path is not None and not path.exists():
                raise FileNotFoundError(
                    f"seed document missing: {doc} (run scripts/gen_fixtures.py)"
                )
            digest = (
                hashlib.sha256(path.read_bytes()).hexdigest()
                if path is not None
                else hashlib.sha256(inv["id"].encode()).hexdigest()
            )
            s.add(
                Invoice(
                    id=inv["id"], user_id=inv["user"], issuer=inv["issuer"], amount_minor=inv["amount_minor"],
                    currency=inv["currency"], due_date=inv["due_date"],
                    beneficiary_payee_id=inv["beneficiary_payee"], status=inv["status"], source_doc_hash=digest,
                    description=inv.get("description"),
                )
            )  # fmt: skip
            n_inv += 1
        for sc in users_cfg["schedules"]:
            s.add(
                Schedule(
                    id=sc["id"], user_id=sc["user"],
                    proposal_template_json={
                        "action": "transfer", "payee": sc["payee"], "account": sc["account"],
                        "amount_minor": sc["amount_minor"], "currency": sc["currency"],
                    },
                    rrule=sc["rrule"], next_run=sc["next_run"], status=sc["status"],
                )
            )  # fmt: skip
        for c in _yaml("cop.yaml", data_dir)["cop"]:  # simulated Confirmation of Payee (synthetic)
            digits = (
                str(c["number"])  # a landlord account the customer types in chat (t19, t20)
                if "number" in c
                else synth.attacker_account_number(c["account_key"], c["last4"])
            )
            s.add(
                CopRegistry(
                    account_no_hash=vault.account_hash(digits, secrets_dir),
                    holder_name=c["holder"], synthetic=True,
                )
            )  # fmt: skip
    vault.write_accounts_file(numbers, secrets_dir)
    return SeedSummary(
        users=len(users_cfg["users"]), accounts=counts["accounts"], payees=counts["payees"],
        providers=len(providers_cfg["providers"]), fx_rates=len(fx), transfers=counts["transfers"],
        credit_events=counts["credit_events"], invoices=n_inv, schedules=len(users_cfg["schedules"]),
        as_of=as_of,
    )  # fmt: skip


def main() -> None:
    """CLI entry point for ``make seed``: migrate then seed the DATABASE_URL database."""
    from corridor.config import AppEnv, get_settings
    from corridor.sandbox.db import get_engine

    if get_settings().app_env is AppEnv.DEMO and "--force" not in __import__("sys").argv:
        raise SystemExit("refusing to wipe the sandbox with APP_ENV=demo without --force")
    engine = get_engine()
    summary = seed(engine)
    print(summary)


if __name__ == "__main__":
    main()
