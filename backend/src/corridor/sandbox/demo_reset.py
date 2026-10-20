"""Reset the sandbox for a demo or a recording: ``make demo-reset``.

Re-seeds every sandbox table and also clears the safety state: safety locks, security events, unused
or used confirmation grants, TOTP time step counters, processed documents, the audit ledger with its
Merkle roots, and the uploaded files under ``data/uploads``. It prints what is left so the reset is
checkable. Refused unless APP_ENV is dev or demo (never test, never anything else).
"""

import shutil

from sqlalchemy import text

from corridor.config import AppEnv, get_settings
from corridor.ledger import verify_chain
from corridor.reader.upload import UPLOAD_DIR
from corridor.sandbox.db import get_engine
from corridor.sandbox.seed import seed

SAFETY_TABLES = (
    "safety_locks", "security_events", "confirmation_grants", "totp_steps", "documents",
    "ledger_entries", "ledger_roots", "parser_traces",
)  # fmt: skip


def reset(engine: object | None = None) -> dict[str, int]:
    """Seed everything again and return the row counts of the safety tables (all zero)."""
    eng = engine or get_engine()
    seed(eng)  # type: ignore[arg-type]
    with eng.begin() as c:  # type: ignore[attr-defined]  # conversation state (LangGraph checkpoints), not the ledger
        for t in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            if c.execute(text("SELECT to_regclass(:t)"), {"t": t}).scalar_one() is not None:
                c.execute(text(f"TRUNCATE {t}"))
    if UPLOAD_DIR.exists():
        shutil.rmtree(UPLOAD_DIR)
    counts: dict[str, int] = {}
    with eng.connect() as c:  # type: ignore[attr-defined]
        for t in SAFETY_TABLES:
            counts[t] = int(c.execute(text(f"SELECT count(*) FROM {t}")).scalar_one())  # noqa: S608
    return counts


def main() -> None:
    if get_settings().app_env not in (AppEnv.DEV, AppEnv.DEMO):
        raise SystemExit("demo-reset runs only with APP_ENV=dev or demo")
    engine = get_engine()
    counts = reset(engine)
    from sqlalchemy.orm import Session

    with Session(engine) as s:
        chain = verify_chain(s)
    print("sandbox re-seeded; safety state after reset:")
    for table, n in counts.items():
        print(f"  {table}: {n}")
    print(f"  ledger chain valid: {chain.valid} ({chain.entries_checked} entries)")


if __name__ == "__main__":
    main()
