"""Agent events streamed to the UI and the bank console (docs/spec/modules.md 5.16)."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from corridor.contracts.scalars import IdStr


class AgentEventType(StrEnum):
    """Every event type the API can emit."""

    INTENT = "intent"
    CLARIFY = "clarify"
    PLAN = "plan"
    STEP_START = "step_start"
    STEP_RESULT = "step_result"
    ROUTE = "route"
    RISK = "risk"
    TAINT_FLAG = "taint_flag"
    POLICY_DECISION = "policy_decision"
    CONFIRM_REQUEST = "confirm_request"
    EXECUTED = "executed"
    REJECTED = "rejected"
    MESSAGE = "message"
    LEDGER_APPEND = "ledger_append"
    GROUNDING = "grounding"
    ERROR = "error"


class AgentEvent(BaseModel):
    """One streamed event. ``data`` must never contain secrets or full account numbers (INV-12)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: AgentEventType
    trace_id: IdStr
    seq: int = Field(ge=0)
    ts: datetime
    data: dict[str, Any] = Field(default_factory=dict)
