"""Change one ledger row the way an attacker with database access would (browser tests only, e2e database).

The append only triggers are disabled for the moment of the edit and put back, then the hash chain no longer
verifies. The console's "verify chain" must say FAIL."""

import os
import sys

from sqlalchemy import text

from corridor.sandbox.db import get_engine


def main() -> int:
    from sqlalchemy.engine import make_url

    if not (make_url(os.environ.get("DATABASE_URL", "x:///")).database or "").endswith("_e2e"):
        print("refusing: the database name must end with _e2e", file=sys.stderr)
        return 2
    seq = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    with get_engine().begin() as c:
        for t in ("ledger_entries",):
            c.execute(text(f"ALTER TABLE {t} DISABLE TRIGGER {t}_append_only"))
        c.execute(
            text("UPDATE ledger_entries SET payload_canonical_json = payload_canonical_json || ' ' WHERE seq = :s"),
            {"s": seq},
        )
        c.execute(text("ALTER TABLE ledger_entries ENABLE TRIGGER ledger_entries_append_only"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
