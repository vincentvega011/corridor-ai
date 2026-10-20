"""Small shared constrained types used across the contracts.

These exist so that length caps and hash formats are enforced in exactly one place.
"""

from typing import Annotated

from pydantic import BeforeValidator, Field, StringConstraints

from corridor.contracts.money import strict_int

ShortText = Annotated[str, StringConstraints(max_length=120)]
"""Free text from any non-system source. The 120 character cap (docs/spec/modules.md 5.5) limits how
much instruction-like content a document or model can smuggle through a typed field."""

IdStr = Annotated[str, StringConstraints(min_length=1, max_length=128)]
"""An opaque identifier (user, account, payee, proposal, trace). Never an account number."""

Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
"""A lowercase hex SHA-256 digest. Used for proposal hashes, ledger hashes and policy hashes."""

Bps = Annotated[int, BeforeValidator(strict_int), Field(ge=0, le=10000)]
"""A ratio in basis points (0 to 10000 means 0.0 to 1.0). Used for scores and confidences so
nothing in the safety path is a float (ADR 0003)."""

SignedBps = Annotated[int, BeforeValidator(strict_int), Field(ge=-10000, le=10000)]
"""A signed contribution in basis points, for example a SHAP value scaled to the score."""
