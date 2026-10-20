"""Tool descriptions and tool results (docs/spec/architecture.md 4.2 step 6, 5.14).

Security reason (INV-1, INV-9): the planner can only call READ tools, so ``ToolKind`` has
no mutating member. Every ToolResult carries the numeric facts it produced; the grounding
verifier later accepts a number in a reply only if it matches one of these facts.
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from corridor.contracts.money import CurrencyCode, MinorUnits, Rate
from corridor.contracts.scalars import IdStr, ShortText


class ToolKind(StrEnum):
    """Kind of tool. Only READ exists: no LLM-callable tool may mutate state (INV-1)."""

    READ = "read"


class ToolSpec(BaseModel):
    """Registration record for a tool the planner may call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: IdStr
    description: ShortText
    kind: ToolKind = ToolKind.READ
    args_schema: dict[str, Any] = Field(default_factory=dict)  # JSON Schema for the arguments


class FactKind(StrEnum):
    """Which numeric family a fact belongs to; decides how the grounding verifier matches it."""

    MONEY = "money"
    RATE = "rate"
    COUNT = "count"
    DATE = "date"


class NumericFact(BaseModel):
    """One number a tool produced, in an exact typed form, for the grounding registry.

    Exactly the field matching ``kind`` must be set; this prevents a vague "number" that
    could match anything.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: ShortText
    kind: FactKind
    amount_minor: MinorUnits | None = None
    currency: CurrencyCode | None = None
    rate: Rate | None = None
    count: StrictInt | None = None
    on: date | None = None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        want: dict[FactKind, tuple[object, ...]] = {
            FactKind.MONEY: (self.amount_minor, self.currency),
            FactKind.RATE: (self.rate,),
            FactKind.COUNT: (self.count,),
            FactKind.DATE: (self.on,),
        }
        used = {
            "amount_minor": self.amount_minor,
            "currency": self.currency,
            "rate": self.rate,
            "count": self.count,
            "on": self.on,
        }
        expected = want[self.kind]
        if any(v is None for v in expected):
            raise ValueError(f"fact of kind {self.kind.value} is missing its value fields")
        n_set = sum(v is not None for v in used.values())
        if n_set != len(expected):
            raise ValueError(f"fact of kind {self.kind.value} has fields from another kind")
        return self

    def as_decimal(self) -> Decimal | None:
        """Return the numeric value as a Decimal for MONEY (minor units), RATE and COUNT."""
        if self.kind is FactKind.MONEY and self.amount_minor is not None:
            return Decimal(self.amount_minor)
        if self.kind is FactKind.RATE:
            return self.rate
        if self.kind is FactKind.COUNT and self.count is not None:
            return Decimal(self.count)
        return None


class ToolResult(BaseModel):
    """The result of one read tool call.

    ``data`` is trusted system data (provenance SYSTEM), but it may contain values that
    originated from earlier untrusted documents; those keep their Tainted wrappers inside
    ``data`` and must not be flattened.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_call_id: IdStr
    tool: IdStr
    data: dict[str, Any]
    facts: tuple[NumericFact, ...] = ()
