"""Deterministic evaluation of NLU conditions on tool facts. The LLM never evaluates a condition.

Supported: ``balance_after_reserve`` with reserve ``rent``: (balance of the source account minus the
reserve) must be at least the amount being sent. The reserve for "rent" is the amount of the user's most
recent transfer to one of their own landlord payees, in the source account's currency. If it cannot be
determined the condition is not met (fail closed), and the agent says why instead of guessing.
"""

from dataclasses import dataclass
from typing import Any

from corridor.contracts.tools import ToolResult


@dataclass(frozen=True)
class ConditionResult:
    """Outcome with the numbers needed to explain it."""

    met: bool
    reason: str  # ok | reserve_unknown | insufficient | unsupported
    balance_minor: int | None = None
    reserve_minor: int | None = None
    needed_minor: int | None = None
    currency: str | None = None


def _fact_amount(result: ToolResult, label: str) -> tuple[int | None, str | None]:
    for f in result.facts:
        if f.label == label and f.amount_minor is not None:
            return f.amount_minor, f.currency.value if f.currency else None
    return None, None


def evaluate_condition(
    condition: dict[str, Any],
    *,
    source_account_id: str,
    send_minor: int,
    balances: ToolResult,
    payees: ToolResult,
    history: ToolResult,
) -> ConditionResult:
    """Evaluate ``condition`` (see module docstring) against read tool results."""
    if condition.get("kind") != "balance_after_reserve" or condition.get("op") != ">=":
        return ConditionResult(False, "unsupported")
    balance, ccy = _fact_amount(balances, f"balance {source_account_id}")
    if balance is None:
        return ConditionResult(False, "reserve_unknown")
    if condition.get("reserve") != "rent":
        return ConditionResult(False, "unsupported", balance, None, send_minor, ccy)
    landlords = {
        p["payee_id"] for p in payees.data.get("payees", []) if p.get("relation") == "landlord"
    }
    rents = [t for t in history.data.get("transfers", []) if t.get("payee_id") in landlords]
    if not rents:
        return ConditionResult(False, "reserve_unknown", balance, None, send_minor, ccy)
    latest = max(rents, key=lambda t: (t.get("date", ""), t.get("transfer_id", "")))
    reserve, rccy = _fact_amount(history, f"transfer {latest['transfer_id']}")
    if reserve is None or rccy != ccy:
        return ConditionResult(False, "reserve_unknown", balance, None, send_minor, ccy)
    met = balance - reserve >= send_minor
    return ConditionResult(met, "ok" if met else "insufficient", balance, reserve, send_minor, ccy)
