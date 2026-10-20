"""Helpers to write and tamper with the ledger in tests (an explicit admin action)."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from corridor.contracts.ledger import LedgerActor, LedgerEventType
from corridor.ledger import Ledger

T0 = datetime(2026, 10, 11, 8, 0, tzinfo=UTC)
PVH = "d" * 64


def append_n(engine: Engine, n: int, *, user: str = "usr_ramesh", session_id: str = "s1") -> None:
    """Append ``n`` entries, one transaction each."""
    for i in range(n):
        with Session(engine) as s, s.begin():
            Ledger().append(
                s, trace_id=f"t{i}", actor=LedgerActor.AGENT, event_type=LedgerEventType.PROPOSAL,
                user_id=user, session_id=session_id, data={"i": i, "amount_minor": 1000 + i},
                policy_version_hash=PVH, now=T0 + timedelta(minutes=i),
            )  # fmt: skip


def admin_sql(engine: Engine, sql: str, params: dict[str, Any] | None = None) -> None:
    """Run ``sql`` against the ledger tables with the append-only triggers switched off.

    This is what a database administrator (not the application) could do, and what tamper tests and
    the tamper demo simulate."""
    with engine.begin() as c:
        for t in ("ledger_entries", "ledger_roots"):
            c.execute(text(f"ALTER TABLE {t} DISABLE TRIGGER {t}_append_only"))
            c.execute(text(f"ALTER TABLE {t} DISABLE TRIGGER {t}_no_truncate"))
        c.execute(text(sql), params or {})
        for t in (
            "ledger_entries",
            "ledger_roots",
        ):  # DDL is transactional: a failure undoes DISABLE
            c.execute(text(f"ALTER TABLE {t} ENABLE TRIGGER {t}_append_only"))
            c.execute(text(f"ALTER TABLE {t} ENABLE TRIGGER {t}_no_truncate"))
