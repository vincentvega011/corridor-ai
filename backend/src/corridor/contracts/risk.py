"""Risk assessment attached to a proposal (docs/spec/modules.md 5.12).

Scores are integer basis points (0 to 10000), never floats, so every risk value can be ledgered
and hashed with canonical JSON (ADR 0003).

Security reason: the policy engine consumes the score and level (R-RISK) and the console
shows the top reasons. The assessment is advisory input to deterministic policy; it is not
part of the WYSIWYS hash because the user does not sign a risk score.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from corridor.contracts.scalars import Bps, IdStr, ShortText, SignedBps


class RiskLevel(StrEnum):
    """Risk band after applying the configured thresholds."""

    LOW = "low"
    HIGH = "high"
    CRITICAL = "critical"


class RiskReason(BaseModel):
    """One explanation for a score, for example a SHAP feature and its human readable text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    feature: ShortText
    text: ShortText
    contribution_bps: SignedBps = 0


class RiskAssessment(BaseModel):
    """Score (basis points), its level, at most 3 reasons, and which scorer produced it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    score_bps: Bps
    level: RiskLevel
    top_reasons: tuple[RiskReason, ...] = Field(default=(), max_length=3)
    model_id: IdStr
    fallback_used: bool = False
