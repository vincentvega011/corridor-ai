"""Policy configuration: typed, loaded from YAML, canonicalised and hashed (spec/modules.md 5.7).

The hash of the canonical form is the ``policy_version_hash`` written into every decision and
ledger entry, so an auditor can tell which exact rules and limits produced a decision. Loading
reads a file; ``evaluate`` never does (INV-2).
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from corridor.contracts.canonical import sha256_hex
from corridor.contracts.scalars import Sha256Hex

DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[3] / "config" / "policy.yaml"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Limits(_Frozen):
    """Tier and daily limits in NPR minor units (equivalents are computed from the FX snapshot)."""

    low_npr_minor: int = Field(gt=0)
    daily_npr_minor: int = Field(gt=0)


class Velocity(_Frozen):
    """Transfers per sliding window."""

    window_minutes: int = Field(gt=0)
    mfa_over: int = Field(ge=0)
    deny_over: int = Field(ge=0)


class RiskThresholds(_Frozen):
    """Risk thresholds and the weights of the deterministic rule score, all in basis points."""

    high_bps: int = Field(ge=0, le=10000)
    critical_bps: int = Field(ge=0, le=10000)
    weights_bps: dict[str, int] = Field(default_factory=dict)
    amount_unusual_multiple: int = Field(default=3, ge=1)
    amount_extreme_multiple: int = Field(default=10, ge=1)
    quiet_hours: tuple[int, int] = (0, 5)


class LockConfig(_Frozen):
    """Safety lock triggers (INV-11)."""

    window_minutes: int = Field(gt=0)
    denied_attempts: int = Field(gt=0)
    totp_failures: int = Field(gt=0)


class ContributionLimits(_Frozen):
    """How often one person may ask the same family member for money (R-CONTRIBUTION)."""

    max_requests_per_contributor_per_day: int = Field(default=3, gt=0)


class PayeeCooloff(_Frozen):
    """R-PAYEE-COOLOFF: an account younger than ``hours`` can receive at most the cap in total."""

    hours: int = Field(gt=0, le=24)
    """At most 24: the running total is built from the last 24 hours of transfers."""
    first_payment_cap_npr_minor: int = Field(ge=0)
    """Total a young account the user typed themselves can receive during the cooling off."""
    untrusted_origin_cap_npr_minor: int = Field(ge=0)
    """Same, for an account that came from UNTRUSTED or MODEL_INFERRED data. 0 is a full hold."""


class PolicyConfig(_Frozen):
    """All tunable policy values plus the hash of their canonical form."""

    version: int
    base_currency: str
    limits: Limits
    new_payee_days: int = Field(ge=0)
    payee_cooloff: PayeeCooloff
    velocity: Velocity
    risk: RiskThresholds
    lock: LockConfig
    contribution: ContributionLimits = ContributionLimits()
    protected_setting_words: tuple[str, ...]
    read_tools: tuple[str, ...]
    version_hash: Sha256Hex


def parse_policy(raw: dict[str, Any]) -> PolicyConfig:
    """Build a PolicyConfig from parsed YAML; the version hash covers exactly ``raw``."""
    return PolicyConfig(**raw, version_hash=sha256_hex(raw))


def load_policy_config(path: Path | None = None) -> PolicyConfig:
    """Load and hash the policy YAML (default ``backend/config/policy.yaml``)."""
    raw = yaml.safe_load((path or DEFAULT_POLICY_PATH).read_text(encoding="utf-8"))
    return parse_policy(raw)
