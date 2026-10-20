"""Ledger entry contract (docs/spec/modules.md 5.10).

Security reason (INV-8): every decision is appended to a hash-chained, tamper-evident audit
ledger. This module defines the entry shape and the single hash formula so the writer and
the verifier cannot disagree. The model checks formats only. It does NOT check that
``entry_hash`` or ``payload_hash`` are consistent, because the verifier must be able to
load tampered rows in order to report the first broken link.

Hash formula: the spec writes ``SHA256(prev_hash || seq || ts || payload_hash ||
policy_version_hash)``. We deviate in two ways (see ADR 0002): the fields are hashed as the
canonical JSON of a list (no delimiter ambiguity), and trace_id, actor and event_type are
included so tampering with those columns is also detected.
"""

import hashlib
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from corridor.contracts.canonical import sha256_hex
from corridor.contracts.scalars import IdStr, Sha256Hex

GENESIS_PREV_HASH = "0" * 64


class LedgerActor(StrEnum):
    """Who caused the event."""

    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class LedgerEventType(StrEnum):
    """Kinds of audited events."""

    TURN_STARTED = "turn_started"
    READER_EXTRACTION = "reader_extraction"
    CASCADE_DECISION = "cascade_decision"
    PROPOSAL = "proposal"
    POLICY_DECISION = "policy_decision"
    CONFIRMATION_GRANTED = "confirmation_granted"
    CONFIRMATION_REJECTED = "confirmation_rejected"
    MFA_ATTEMPT = "mfa_attempt"
    EXECUTION = "execution"
    EXECUTION_FAILED = "execution_failed"
    REJECTION = "rejection"
    PROPOSAL_DISCARDED = "proposal_discarded"
    SAFETY_LOCK = "safety_lock"
    GROUNDING_FAILURE = "grounding_failure"
    PASSPORT_ISSUED = "passport_issued"
    PASSPORT_SHARED = "passport_shared"
    VERIFICATION_REQUESTED = "verification_requested"
    CONTRIBUTION_RESPONDED = "contribution_responded"


class LedgerEntry(BaseModel):
    """One append-only ledger row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    seq: int = Field(ge=1)
    ts: datetime
    trace_id: IdStr
    actor: LedgerActor
    event_type: LedgerEventType
    payload_canonical_json: str
    payload_hash: Sha256Hex
    policy_version_hash: Sha256Hex
    prev_hash: Sha256Hex
    entry_hash: Sha256Hex

    @field_validator("ts")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("ledger timestamps must be timezone aware")
        return v


def payload_hash_of(payload_canonical_json: str) -> str:
    """Return the SHA-256 of the stored payload text (single source for writer and verifier)."""
    return hashlib.sha256(payload_canonical_json.encode("utf-8")).hexdigest()


def compute_entry_hash(
    prev_hash: str,
    seq: int,
    ts: datetime,
    trace_id: str,
    actor: LedgerActor,
    event_type: LedgerEventType,
    payload_hash: str,
    policy_version_hash: str,
) -> str:
    """Return the entry hash for the given fields (the single source of the formula).

    Covers every column except ``entry_hash`` and ``payload_canonical_json`` (which is
    covered through ``payload_hash``), so changing actor, event type or trace id breaks
    the chain.
    """
    return sha256_hex(
        [prev_hash, seq, ts, trace_id, actor, event_type, payload_hash, policy_version_hash]
    )
