"""Plan DAG (docs/spec/architecture.md 4.2 step 5, 5.15).

Security reason (INV-10): a plan is validated structurally before anything runs. A cyclic,
dangling or oversized plan is rejected at construction, so downstream code never has to
handle a malformed graph. Tool registration and "reads before proposals" are checked later
by the agent, which knows the tool registry.
"""

from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from corridor.contracts.provenance import Tainted
from corridor.contracts.scalars import IdStr

MAX_PLAN_STEPS = 12


class StepKind(StrEnum):
    """READ steps call read-only tools; PROPOSE steps emit an ActionProposal (never execute)."""

    READ = "READ"
    PROPOSE = "PROPOSE"


class PlanStep(BaseModel):
    """One node of the plan. Args are Tainted so provenance flows into proposals."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: IdStr
    kind: StepKind
    tool: IdStr
    args: dict[str, Tainted[Any]] = Field(default_factory=dict)
    depends_on: tuple[IdStr, ...] = ()


class Plan(BaseModel):
    """A validated DAG: unique ids, existing dependencies, no cycles, at most 12 steps."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    steps: tuple[PlanStep, ...]

    @model_validator(mode="after")
    def _validate_dag(self) -> Self:
        if len(self.steps) > MAX_PLAN_STEPS:
            raise ValueError(f"plan has {len(self.steps)} steps; the maximum is {MAX_PLAN_STEPS}")
        ids = [s.id for s in self.steps]
        if len(set(ids)) != len(ids):
            raise ValueError("plan step ids must be unique")
        known = set(ids)
        for step in self.steps:
            if step.id in step.depends_on:
                raise ValueError(f"step {step.id} depends on itself")
            missing = [d for d in step.depends_on if d not in known]
            if missing:
                raise ValueError(f"step {step.id} depends on unknown steps {missing}")
        # Kahn's algorithm: if not every node can be removed, there is a cycle.
        indegree = {s.id: len(set(s.depends_on)) for s in self.steps}
        dependents: dict[str, list[str]] = {i: [] for i in ids}
        for step in self.steps:
            for dep in set(step.depends_on):
                dependents[dep].append(step.id)
        ready = [i for i, n in indegree.items() if n == 0]
        removed = 0
        while ready:
            node = ready.pop()
            removed += 1
            for nxt in dependents[node]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    ready.append(nxt)
        if removed != len(ids):
            raise ValueError("plan contains a dependency cycle")
        return self
