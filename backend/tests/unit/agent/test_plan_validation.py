"""Plan validation (docs/spec/modules.md 5.15): registered tools only, bounded, acyclic, READ before PROPOSE."""

import pytest

from corridor.agent.planning import PlanInvalid, validate_plan
from corridor.contracts.action import ActionType
from corridor.contracts.intent import IntentType
from corridor.contracts.llm_outputs import PlannerOutput


def out(steps: list[dict], proposal: dict | None = None) -> PlannerOutput:
    return PlannerOutput.model_validate({"steps": steps, "proposal": proposal})


def read(i: str, tool: str = "get_balances", deps: list[str] | None = None, **args: object) -> dict:
    return {"id": i, "kind": "READ", "tool": tool, "args": args, "depends_on": deps or []}


SEND = {"action": "transfer", "source_account_ref": "acc1", "payee_ref": "pay1", "rationale": "x"}


def check(o: PlannerOutput, intent: IntentType = IntentType.SEND_MONEY, payee: str | None = "pay1"):
    return validate_plan(o, intent=intent, resolved_payee_id=payee)


def test_valid_plan_gets_a_propose_step_last() -> None:
    v = check(out([read("s1")], SEND))
    assert [s.kind.value for s in v.plan.steps] == ["READ", "PROPOSE"]
    assert v.plan.steps[-1].depends_on == ("s1",)


def test_unregistered_tool_is_rejected() -> None:
    with pytest.raises(PlanInvalid):
        check(out([read("s1", "transfer_money")], SEND))


@pytest.mark.parametrize("tool", ["compare_routes", "plan_tuition_funding", "list_passports"])
def test_a_planner_cannot_name_a_tool_that_only_trusted_code_runs(tool: str) -> None:
    """P3-1: routes, funding plans and passport lists are computed by code, never chosen by a model."""
    with pytest.raises(PlanInvalid):
        check(out([read("s1", tool)], SEND))


def test_the_planner_is_not_offered_the_code_run_tools() -> None:
    from corridor.agent.planning import build_planner_messages

    body = build_planner_messages({})[-1]["content"]
    assert (
        "plan_tuition_funding" not in body
        and "compare_routes" not in body
        and "list_passports" not in body
    )
    assert "get_balances" in body


def test_model_cannot_plan_a_propose_step() -> None:
    bad = {"id": "s1", "kind": "PROPOSE", "tool": "propose_transfer", "args": {}, "depends_on": []}
    with pytest.raises(PlanInvalid):
        check(out([bad], SEND))


def test_cycle_is_rejected() -> None:
    with pytest.raises(PlanInvalid):
        check(out([read("a", deps=["b"]), read("b", "get_payees", deps=["a"])], SEND))


def test_dangling_dependency_is_rejected() -> None:
    with pytest.raises(PlanInvalid):
        check(out([read("a", deps=["zzz"])], SEND))


def test_more_than_twelve_steps_is_rejected() -> None:
    steps = [read(f"s{i}", "get_history", days=i + 1) for i in range(12)]
    with pytest.raises(PlanInvalid):  # 12 reads plus the propose step is 13
        check(out(steps, SEND))


def test_planner_output_schema_caps_steps() -> None:
    steps = [read(f"s{i}", "get_history", days=i + 1) for i in range(13)]
    with pytest.raises(ValueError):
        out(steps, None)


def test_invalid_tool_arguments_are_rejected() -> None:
    with pytest.raises(PlanInvalid):
        check(out([read("s1", "get_fx_quote", pair="NOPE")], SEND))


def test_payment_intent_without_proposal_is_rejected() -> None:
    with pytest.raises(PlanInvalid):
        check(out([read("s1")], None))


def test_read_intent_with_proposal_is_rejected() -> None:
    with pytest.raises(PlanInvalid):
        check(out([read("s1")], SEND), intent=IntentType.CHECK_BALANCE, payee=None)


def test_wrong_action_for_intent_is_rejected() -> None:
    with pytest.raises(PlanInvalid):
        check(out([], {**SEND, "action": ActionType.SCHEDULE.value}))


def test_payee_other_than_resolved_is_rejected() -> None:
    with pytest.raises(PlanInvalid):
        check(out([], {**SEND, "payee_ref": "pay_other"}))


def test_planner_may_not_set_an_amount() -> None:
    with pytest.raises(PlanInvalid):
        check(out([], {**SEND, "amount_minor": 5_000_000_00, "currency": "NPR"}))


def test_invoice_must_belong_to_the_user() -> None:
    draft = {"action": "pay_invoice", "source_account_ref": "acc1", "invoice_ref": "inv_other"}
    with pytest.raises(PlanInvalid):
        validate_plan(
            out([], draft), intent=IntentType.PAY_INVOICE, resolved_payee_id=None,
            allowed_invoice_ids=frozenset({"inv_mine"}),
        )  # fmt: skip
