"""Hash-chained, tamper-evident audit ledger (docs/spec/modules.md 5.10, INV-8)."""

from corridor.ledger.chain import (
    LEDGER_ROLE,
    Ledger,
    LedgerError,
    VerifyResult,
    compute_roots,
    proof_for,
    verify_chain,
)
from corridor.ledger.merkle import inclusion_proof, merkle_root, verify_inclusion

__all__ = [
    "LEDGER_ROLE",
    "Ledger",
    "LedgerError",
    "VerifyResult",
    "compute_roots",
    "inclusion_proof",
    "merkle_root",
    "proof_for",
    "verify_chain",
    "verify_inclusion",
]
