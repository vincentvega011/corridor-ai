"""The planner step: prompt, call, and the checks that make its output safe to use (docs/spec/modules.md 5.15).

The planner (role ``planner``) returns a ``PlannerOutput``: READ steps plus at most one ``ProposalDraft``.
It is untrusted output. ``validate_plan`` turns it into a contracts ``Plan`` and applies the agent
checks: registered READ tools only, valid arguments, at most 12 steps, acyclic, READ before PROPOSE, and
the draft may only name the resolved payee and the action the user's intent allows. Values that matter
(amount, payee, account, provenance, idempotency key) are never taken from the draft; trusted code sets
them (``proposals.py``). A contradicting draft fails closed (INV-1, INV-4).
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from corridor.contracts.action import ActionType
from corridor.contracts.intent import IntentType
from corridor.contracts.llm_outputs import PlannerOutput, ProposalDraft
from corridor.contracts.plan import MAX_PLAN_STEPS, Plan, PlanStep, StepKind
from corridor.contracts.provenance import Provenance, Tainted
from corridor.sandbox.read_tools import CODE_RUN_TOOLS, READ_TOOLS, tool_specs

PROMPT_PATH = Path(__file__).parent / "prompts" / "planner_v1.txt"
PROMPT_VERSION = "planner_v1"
ACTION_FOR_INTENT: dict[IntentType, ActionType] = {
    IntentType.SEND_MONEY: ActionType.TRANSFER,
    IntentType.PAY_INVOICE: ActionType.PAY_INVOICE,
}
PROPOSE_TOOL = {
    ActionType.TRANSFER: "propose_transfer",
    ActionType.PAY_INVOICE: "propose_pay_invoice",
}


class PlanInvalid(Exception):
    """The planner output failed validation. The turn fails closed with a safe message."""


@dataclass(frozen=True)
class ValidatedPlan:
    """A checked plan: the contracts Plan, the proposal draft (or None) and notes about overrides."""

    plan: Plan
    read_calls: tuple[tuple[str, str, dict[str, Any]], ...]  # (step id, tool, validated args)
    draft: ProposalDraft | None
    notes: tuple[str, ...] = field(default=())


def build_planner_messages(context: dict[str, Any]) -> list[dict[str, Any]]:
    """System prompt plus one user message. ``context`` holds only masked, prompt safe values
    (INV-12): names with ``****1234``, money formatted as ``NPR 20,000.00``, ids, ISO dates."""
    system = PROMPT_PATH.read_text(encoding="utf-8").strip()
    tools = [
        {"name": s.name, "description": s.description, "args": s.args_schema.get("properties", {})}
        for s in tool_specs()
        if s.name not in CODE_RUN_TOOLS
    ]
    body = {**context, "tools": tools}
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(body, ensure_ascii=False)},
    ]


def validate_plan(
    out: PlannerOutput,
    *,
    intent: IntentType,
    resolved_payee_id: str | None,
    allowed_invoice_ids: frozenset[str] = frozenset(),
) -> ValidatedPlan:
    """Apply the agent checks to a planner output. Raises PlanInvalid on any violation."""
    expected = ACTION_FOR_INTENT.get(intent)
    steps: list[PlanStep] = []
    calls: list[tuple[str, str, dict[str, Any]]] = []
    seen: set[tuple[str, str]] = set()
    for d in out.steps:
        if d.kind is not StepKind.READ:
            raise PlanInvalid("only READ steps may be planned; proposals go in the proposal field")
        tool = READ_TOOLS.get(d.tool)
        if tool is None or d.tool in CODE_RUN_TOOLS:
            raise PlanInvalid(f"unregistered tool {d.tool}")
        raw_args = {k: (list(v) if isinstance(v, tuple) else v) for k, v in d.args.items()}
        try:
            args = tool.args_model.model_validate(raw_args).model_dump(
                mode="json", exclude_none=True
            )
        except ValidationError as exc:
            raise PlanInvalid(f"invalid arguments for {d.tool}") from exc
        key = (d.tool, json.dumps(args, sort_keys=True))
        if key in seen:
            continue  # a repeated identical call is dropped, not an error
        seen.add(key)
        steps.append(
            PlanStep(
                id=d.id, kind=StepKind.READ, tool=d.tool,
                args={k: Tainted[Any](value=v, provenance=Provenance.MODEL_INFERRED, source_ref="planner") for k, v in args.items()},
                depends_on=d.depends_on,
            )
        )  # fmt: skip
        calls.append((d.id, d.tool, args))
    notes: list[str] = []
    draft = out.proposal
    if expected is None:
        if draft is not None:
            raise PlanInvalid("a read only request must not carry a proposal")
    else:
        if draft is None:
            raise PlanInvalid("a payment request needs a proposal")
        if draft.action is not expected:
            raise PlanInvalid("the proposed action does not match the user's intent")
        if expected is ActionType.TRANSFER and draft.payee_ref != resolved_payee_id:
            raise PlanInvalid("the proposal names a payee other than the resolved one")  # INV-4
        if expected is ActionType.PAY_INVOICE and draft.invoice_ref not in allowed_invoice_ids:
            raise PlanInvalid("the proposal names an invoice the user does not have")
        if draft.amount_minor is not None:
            raise PlanInvalid(
                "the proposal must not set an amount"
            )  # amount comes from the user's words
        notes.append(
            "amount, payee, source account, provenance and idempotency key are set by trusted code"
        )
    try:
        propose_step = (
            [
                PlanStep(
                    id="propose", kind=StepKind.PROPOSE, tool=PROPOSE_TOOL[expected], args={},
                    depends_on=tuple(s.id for s in steps),
                )
            ]
            if expected is not None and draft is not None
            else []
        )  # fmt: skip
        plan = Plan(steps=(*steps, *propose_step))
    except ValidationError as exc:
        raise PlanInvalid(
            "plan structure invalid (cycle, dangling dependency or too many steps)"
        ) from exc
    if len(plan.steps) > MAX_PLAN_STEPS:
        raise PlanInvalid("too many steps")
    return ValidatedPlan(plan, tuple(calls), draft, tuple(notes))


def default_read_plan(intent: IntentType, calls: list[tuple[str, dict[str, Any]]]) -> ValidatedPlan:
    """A deterministic plan for read only intents (no LLM): the given (tool, args) calls."""
    steps = [
        PlanStep(
            id=f"s{i + 1}", kind=StepKind.READ, tool=t,
            args={k: Tainted[Any](value=v, provenance=Provenance.SYSTEM, source_ref="agent") for k, v in a.items()},
        )
        for i, (t, a) in enumerate(calls)
    ]  # fmt: skip
    for s in steps:
        if s.tool not in READ_TOOLS:
            raise PlanInvalid(f"unregistered tool {s.tool}")
    return ValidatedPlan(
        Plan(steps=tuple(steps)),
        tuple((s.id, s.tool, {k: v.value for k, v in s.args.items()}) for s in steps),
        None,
        (f"deterministic plan for {intent.value}",),
    )
