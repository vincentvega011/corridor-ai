"""Append to and verify the hash-chained, tamper-evident audit ledger (docs/spec/modules.md 5.10).

Appends run inside the caller's transaction, so a ledger entry commits or rolls back together
with the state change it records (INV-8). The insert is executed after
``SET LOCAL ROLE corridor_ledger``, a role with INSERT and SELECT only on the ledger tables
(migration 0004), and a Postgres advisory lock serialises appends so ``seq`` stays contiguous. If
anything fails, ``LedgerError`` is raised and the caller must fail the action (fail closed).

The ledger is hash-chained, not a blockchain: each entry includes the hash of the previous one, so a
change to any column of any row breaks every later link.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from corridor.contracts.canonical import canonical_json
from corridor.contracts.ledger import (
    GENESIS_PREV_HASH,
    LedgerActor,
    LedgerEntry,
    LedgerEventType,
    compute_entry_hash,
    payload_hash_of,
)
from corridor.ledger.merkle import inclusion_proof, merkle_root, verify_inclusion

LEDGER_ROLE = "corridor_ledger"
LEDGER_LOCK_KEY = 0x434F5252  # arbitrary constant: all appends take the same advisory lock


class LedgerError(Exception):
    """A ledger write failed. The action that needed it must fail too (INV-8, INV-10)."""


class Ledger:
    """Writer for the audit ledger. Stateless: all state is in the database."""

    def append(
        self,
        session: Session,
        *,
        trace_id: str,
        actor: LedgerActor,
        event_type: LedgerEventType,
        user_id: str | None,
        session_id: str | None,
        data: Mapping[str, Any],
        policy_version_hash: str,
        now: datetime,
    ) -> LedgerEntry:
        """Append one entry inside ``session``'s transaction and return it."""
        try:
            payload = canonical_json({**data, "user_id": user_id, "session_id": session_id})
            payload_hash = payload_hash_of(payload)
            now = now.astimezone(UTC)
            conn = session.connection()
            conn.execute(text(f"SET LOCAL ROLE {LEDGER_ROLE}"))
            conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": LEDGER_LOCK_KEY})
            last = conn.execute(
                text("SELECT seq, entry_hash FROM ledger_entries ORDER BY seq DESC LIMIT 1")
            ).first()
            seq, prev = (1, GENESIS_PREV_HASH) if last is None else (int(last[0]) + 1, str(last[1]))
            entry_hash = compute_entry_hash(
                prev, seq, now, trace_id, actor, event_type, payload_hash, policy_version_hash
            )
            conn.execute(
                text(
                    "INSERT INTO ledger_entries (seq, ts, trace_id, actor, event_type, "
                    "payload_canonical_json, payload_hash, policy_version_hash, prev_hash, "
                    "entry_hash) VALUES (:seq, :ts, :trace, :actor, :event, :payload, :ph, :pvh, "
                    ":prev, :eh)"
                ),
                {
                    "seq": seq, "ts": now, "trace": trace_id, "actor": actor.value,
                    "event": event_type.value, "payload": payload, "ph": payload_hash,
                    "pvh": policy_version_hash, "prev": prev, "eh": entry_hash,
                },
            )  # fmt: skip
            conn.execute(text("RESET ROLE"))
            return LedgerEntry(
                seq=seq, ts=now, trace_id=trace_id, actor=actor, event_type=event_type,
                payload_canonical_json=payload, payload_hash=payload_hash,
                policy_version_hash=policy_version_hash, prev_hash=prev, entry_hash=entry_hash,
            )  # fmt: skip
        except Exception as exc:
            raise LedgerError(type(exc).__name__) from exc


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of verifying the whole chain."""

    valid: bool
    entries_checked: int
    first_broken_seq: int | None = None
    reason: str | None = None


def _row_to_hashes(row: Any) -> tuple[str, str]:
    """Recompute (payload_hash, entry_hash) of a raw row; raises on malformed data."""
    ts: datetime = row.ts
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError("naive timestamp")
    payload_hash = payload_hash_of(row.payload_canonical_json)
    entry_hash = compute_entry_hash(
        row.prev_hash, row.seq, ts, row.trace_id, LedgerActor(row.actor),
        LedgerEventType(row.event_type), row.payload_hash, row.policy_version_hash,
    )  # fmt: skip
    return payload_hash, entry_hash


def verify_chain(session: Session) -> VerifyResult:
    """Recompute the whole chain and report the first broken link.

    Checks: first seq is 1 with the genesis prev hash; seq is contiguous; every prev_hash equals the
    previous entry hash; payload_hash matches the stored payload text, which must be canonical JSON;
    entry_hash matches its columns; timestamps are timezone aware; every stored Merkle root still
    reproduces. Any exception while verifying means the chain is broken (never a crash).
    """
    expected_seq, prev, checked = 1, GENESIS_PREV_HASH, 0
    try:
        # Roots first: they are anchored only after their entries commit, so they are always a
        # prefix of the entries read next (no false alarm while a payment commits concurrently).
        roots = session.execute(
            text(
                "SELECT scope, scope_key, first_seq, last_seq, entry_count, root_hash "
                "FROM ledger_roots ORDER BY id"
            )
        ).all()
        rows = session.execute(
            text(
                "SELECT seq, ts, trace_id, actor, event_type, payload_canonical_json, "
                "payload_hash, policy_version_hash, prev_hash, entry_hash "
                "FROM ledger_entries ORDER BY seq"
            )
        ).all()
    except Exception:
        return VerifyResult(False, 0, None, "unreadable")
    for row in rows:
        checked += 1
        seq = int(row.seq)
        try:
            if seq != expected_seq:
                return VerifyResult(False, checked, expected_seq, "sequence gap or reorder")
            if row.prev_hash != prev:
                return VerifyResult(False, checked, seq, "prev_hash does not match")
            payload_hash, entry_hash = _row_to_hashes(row)
            if payload_hash != row.payload_hash:
                return VerifyResult(False, checked, seq, "payload does not match payload_hash")
            if canonical_json(json.loads(row.payload_canonical_json)) != row.payload_canonical_json:
                return VerifyResult(False, checked, seq, "payload is not canonical JSON")
            if entry_hash != row.entry_hash:
                return VerifyResult(False, checked, seq, "entry_hash does not match columns")
        except Exception as exc:
            return VerifyResult(False, checked, seq, f"malformed row ({type(exc).__name__})")
        prev, expected_seq = row.entry_hash, seq + 1
    broken = _verify_roots(list(roots), list(rows))
    if broken is not None:
        return VerifyResult(False, checked, broken[0], broken[1])
    return VerifyResult(True, checked)


def scope_key(scope: str, row: Any) -> str | None:
    """The Merkle scope key of a raw row: the UTC day, or the session id from the payload."""
    if scope == "day":
        ts: datetime = row.ts
        return ts.astimezone(UTC).strftime("%Y-%m-%d")
    session_id = json.loads(row.payload_canonical_json).get("session_id")
    return None if session_id is None else str(session_id)


def _leaves(rows: list[Any], scope: str, key: str, last_seq: int) -> list[Any]:
    return [r for r in rows if r.seq <= last_seq and scope_key(scope, r) == key]


def _verify_roots(roots: list[Any], rows: list[Any]) -> tuple[int, str] | None:
    try:
        by_seq = {int(r.seq) for r in rows}
        for root in roots:
            leaves = _leaves(rows, root.scope, root.scope_key, int(root.last_seq))
            hashes = [r.entry_hash for r in leaves]
            if (
                len(leaves) != root.entry_count
                or merkle_root(hashes) != root.root_hash
                or int(root.last_seq) not in by_seq
            ):
                return int(root.last_seq), "stored Merkle root no longer reproduces"
    except Exception:
        return 0, "Merkle roots unreadable"
    return None


def compute_roots(session: Session, now: datetime) -> int:
    """Store an immutable Merkle root snapshot per day and per session. Returns rows written."""
    rows = session.execute(
        text("SELECT seq, ts, entry_hash, payload_canonical_json FROM ledger_entries ORDER BY seq")
    ).all()
    groups: dict[tuple[str, str], list[Any]] = {}
    for r in rows:
        for scope in ("day", "session"):
            key = scope_key(scope, r)
            if key is not None:
                groups.setdefault((scope, key), []).append(r)
    written = 0
    conn = session.connection()
    conn.execute(text(f"SET LOCAL ROLE {LEDGER_ROLE}"))
    for (scope, key), members in groups.items():
        hashes = [m.entry_hash for m in members]
        root = merkle_root(hashes)
        last = int(members[-1].seq)
        exists = conn.execute(
            text(
                "SELECT 1 FROM ledger_roots WHERE scope = :s AND scope_key = :k "
                "AND last_seq = :l AND root_hash = :r"
            ),
            {"s": scope, "k": key, "l": last, "r": root},
        ).first()
        if exists:
            continue
        conn.execute(
            text(
                "INSERT INTO ledger_roots (scope, scope_key, first_seq, last_seq, entry_count, "
                "root_hash, computed_at) VALUES (:s, :k, :f, :l, :n, :r, :t)"
            ),
            {"s": scope, "k": key, "f": int(members[0].seq), "l": last, "n": len(members),
             "r": root, "t": now.astimezone(UTC)},
        )  # fmt: skip
        written += 1
    conn.execute(text("RESET ROLE"))
    return written


def proof_for(session: Session, seq: int) -> dict[str, Any] | None:
    """A day scoped inclusion proof for entry ``seq`` (None if there is no such entry)."""
    rows = session.execute(
        text("SELECT seq, ts, entry_hash, payload_canonical_json FROM ledger_entries ORDER BY seq")
    ).all()
    target = next((r for r in rows if int(r.seq) == seq), None)
    if target is None:
        return None
    key = scope_key("day", target)
    members = [r for r in rows if scope_key("day", r) == key]
    hashes = [m.entry_hash for m in members]
    index = next(i for i, m in enumerate(members) if int(m.seq) == seq)
    proof = inclusion_proof(hashes, index)
    root = merkle_root(hashes)
    return {
        "seq": seq, "entry_hash": target.entry_hash, "scope": "day", "scope_key": key,
        "root": root, "index": index, "leaf_count": len(hashes),
        "proof": [{"side": side, "hash": h} for side, h in proof],
        "valid": verify_inclusion(target.entry_hash, proof, root),
    }  # fmt: skip
