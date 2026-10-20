"""Trace store for the parser's own output (P3-4b, ADR 0019 addendum).

The hash chained ledger holds only ``parser_output_hash``. The masked JSON lives here, outside the
chain, so it can expire: ``RETENTION_DAYS`` (7) after the turn the row is deleted (pruned on every
write) and the officer console shows "expired". The text was masked by ``mask_account_numbers``
before it got here (INV-12) and holds the user's own words as slots, never document text (INV-3).
No model reads it.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from corridor.sandbox.models import ParserTrace

RETENTION_DAYS = 7


def reference_hash(trace_id: str, masked_output: str) -> str:
    """SHA-256 over the trace id and the text, so the same output in two turns differs."""
    return hashlib.sha256(f"{trace_id}\n{masked_output}".encode()).hexdigest()


def store(
    session: Session, *, trace_id: str, user_id: str, masked_output: str, now: datetime
) -> str:
    """Save one masked output, return its reference hash and prune expired rows (empty: nothing)."""
    session.execute(delete(ParserTrace).where(ParserTrace.expires_at <= now))
    if not masked_output:
        return ""
    ref = reference_hash(trace_id, masked_output)
    if session.get(ParserTrace, ref) is None:
        session.add(
            ParserTrace(
                ref_hash=ref, user_id=user_id, created_at=now,
                expires_at=now + timedelta(days=RETENTION_DAYS), masked_output=masked_output,
            )
        )  # fmt: skip
    return ref


def lookup(session: Session, refs: list[str], now: datetime) -> dict[str, str | None]:
    """Reference hash to text, or None when it has expired (or never existed)."""
    wanted = [r for r in dict.fromkeys(refs) if r]
    rows = session.execute(
        select(ParserTrace.ref_hash, ParserTrace.masked_output).where(
            ParserTrace.ref_hash.in_(wanted), ParserTrace.expires_at > now
        )
    ).all()
    found = {r[0]: r[1] for r in rows}
    return {r: found.get(r) for r in wanted}
