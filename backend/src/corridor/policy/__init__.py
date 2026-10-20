"""Deterministic policy engine (docs/spec/modules.md 5.7, INV-2 and INV-7)."""

from corridor.policy.config import PolicyConfig, load_policy_config, parse_policy
from corridor.policy.engine import evaluate, evaluate_read
from corridor.policy.rules import OUTGOING, RULES, RuleResult
from corridor.policy.state import AccountInfo, AccountState, InvoiceInfo, PaidItem, PayeeInfo

__all__ = [
    "OUTGOING",
    "RULES",
    "AccountInfo",
    "AccountState",
    "InvoiceInfo",
    "PaidItem",
    "PayeeInfo",
    "PolicyConfig",
    "RuleResult",
    "evaluate",
    "evaluate_read",
    "load_policy_config",
    "parse_policy",
]
