"""Policy decisions and the most-restrictive-wins combination (INV-7).

Security reason: the combined decision of many rules must be the most restrictive of them
so no rule can relax another. ``combine`` is the only sanctioned way to merge decisions;
it is a max over a total order, hence associative, commutative and idempotent.
"""

from collections.abc import Iterable
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from corridor.contracts.scalars import Sha256Hex


class Decision(StrEnum):
    """Outcome of policy evaluation, ordered by restrictiveness (see ``rank``)."""

    ALLOW = "allow"
    REQUIRE_CONFIRM = "require_confirm"
    REQUIRE_MFA = "require_mfa"
    DENY = "deny"

    @property
    def rank(self) -> int:
        """0 for ALLOW up to 3 for DENY. Higher is more restrictive."""
        return _RANK[self]

    @classmethod
    def combine(cls, decisions: Iterable["Decision"]) -> "Decision":
        """Return the most restrictive decision. See module level ``combine``."""
        return combine(decisions)


_RANK: dict[Decision, int] = {
    Decision.ALLOW: 0,
    Decision.REQUIRE_CONFIRM: 1,
    Decision.REQUIRE_MFA: 2,
    Decision.DENY: 3,
}


def combine(decisions: Iterable[Decision]) -> Decision:
    """Most restrictive wins: DENY > REQUIRE_MFA > REQUIRE_CONFIRM > ALLOW.

    An empty collection yields DENY: no rule spoke, so nothing is permitted (INV-10).
    """
    result: Decision | None = None
    for d in decisions:
        if result is None or d.rank > result.rank:
            result = d
    return Decision.DENY if result is None else result


class PolicyDecision(BaseModel):
    """A decision with the rule ids and reasons that produced it, and the policy version."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: Decision
    rule_ids: tuple[str, ...]
    reasons: tuple[str, ...] = ()
    rule_results: tuple[tuple[str, str, str], ...] = ()
    """Every rule that spoke: (rule id, its own decision, its reason key). For the bank
    console's trace."""
    policy_version_hash: Sha256Hex

    @model_validator(mode="after")
    def _explained(self) -> Self:
        if not self.rule_ids:
            raise ValueError("a policy decision must name at least one rule id (INV-8)")
        return self
