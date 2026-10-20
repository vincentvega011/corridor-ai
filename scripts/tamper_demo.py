"""Show that the audit ledger detects tampering (docs/spec/modules.md 5.10). DEV ONLY.

    make tamper-demo

Creates a scratch database, writes a few ledger entries through the normal application path, verifies
the chain, then uses an explicit ADMIN connection (append-only triggers switched off, which the
application cannot do) to change one stored amount. The verifier reports the first broken link. The
original row is then restored and the chain verifies again. Nothing touches the real dev database.
"""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from corridor.config import AppEnv, get_settings  # noqa: E402
from corridor.contracts.ledger import LedgerActor, LedgerEventType  # noqa: E402
from corridor.ledger import Ledger, compute_roots, verify_chain  # noqa: E402

SCRATCH = "corridor_tamper_demo"


def verify(engine) -> str:  # type: ignore[no-untyped-def]
    with Session(engine) as s:
        r = verify_chain(s)
    if r.valid:
        return f"VALID    ({r.entries_checked} entries checked)"
    return f"BROKEN   first broken link at seq {r.first_broken_seq}: {r.reason}"


def admin(engine, sql: str, params: dict | None = None) -> None:  # type: ignore[no-untyped-def,type-arg]
    with engine.begin() as c:
        c.execute(text("ALTER TABLE ledger_entries DISABLE TRIGGER ledger_entries_append_only"))
        c.execute(text(sql), params or {})
        c.execute(text("ALTER TABLE ledger_entries ENABLE TRIGGER ledger_entries_append_only"))


def main() -> None:
    settings = get_settings()
    if settings.app_env is not AppEnv.DEV:
        raise SystemExit("tamper demo runs only with APP_ENV=dev")
    base = make_url(settings.database_url)
    server = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with server.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)'))
        c.execute(text(f'CREATE DATABASE "{SCRATCH}"'))
    url = base.set(database=SCRATCH).render_as_string(hide_password=False)
    cfg = Config(str(ROOT / "backend" / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    try:
        t0 = datetime.now(UTC)
        for i in range(8):
            with Session(engine) as s, s.begin():
                Ledger().append(
                    s, trace_id=f"demo-{i}", actor=LedgerActor.SYSTEM,
                    event_type=LedgerEventType.EXECUTION, user_id="usr_demo", session_id="demo",
                    data={"transfer_id": f"tr_demo_{i}", "amount_minor": 10_000 * (i + 1), "currency": "AUD"},
                    policy_version_hash="0" * 64, now=t0 + timedelta(seconds=i),
                )  # fmt: skip
        with Session(engine) as s, s.begin():
            compute_roots(s, t0)
        print("1. wrote 8 ledger entries and a Merkle root snapshot")
        print("2. verify:", verify(engine))
        with engine.connect() as c:
            original = c.execute(
                text("SELECT payload_canonical_json FROM ledger_entries WHERE seq = 4")
            ).scalar_one()
        print("3. ADMIN tampers with entry 4 (amount 40000 -> 1), the application could not do this")
        admin(engine, "UPDATE ledger_entries SET payload_canonical_json = :p WHERE seq = 4",
              {"p": original.replace('"amount_minor":40000', '"amount_minor":1')})  # fmt: skip
        print("4. verify:", verify(engine))
        print("5. restoring the original row")
        admin(engine, "UPDATE ledger_entries SET payload_canonical_json = :p WHERE seq = 4", {"p": original})
        print("6. verify:", verify(engine))
        print("7. delete the newest entry (caught by the stored Merkle root)")
        with engine.connect() as c:
            row = c.execute(text("SELECT * FROM ledger_entries WHERE seq = 8")).one()
        admin(engine, "DELETE FROM ledger_entries WHERE seq = 8")
        print("8. verify:", verify(engine))
        admin(
            engine,
            "INSERT INTO ledger_entries VALUES (:seq, :ts, :tr, :ac, :ev, :pj, :ph, :pv, :pr, :eh)",
            {"seq": row.seq, "ts": row.ts, "tr": row.trace_id, "ac": row.actor, "ev": row.event_type,
             "pj": row.payload_canonical_json, "ph": row.payload_hash, "pv": row.policy_version_hash,
             "pr": row.prev_hash, "eh": row.entry_hash},
        )  # fmt: skip
        print("9. restored; verify:", verify(engine))
    finally:
        engine.dispose()
        with server.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)'))


if __name__ == "__main__":
    main()
