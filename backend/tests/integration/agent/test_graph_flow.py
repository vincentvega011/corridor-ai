"""Graph routing, confirmation interrupt and the temporary executor (P1, ADR 0005)."""

import pytest
from sqlalchemy import Engine

from agent_fakes import ScriptedGateway
from agent_helpers import balance, code_for, collect, transfer_count, types
from corridor.agent.service import AgentService, NoPendingConfirmation, ThreadNotFound
from corridor.contracts.action import ActionProposal
from corridor.contracts.events import AgentEventType as ET
from corridor.llm import LLMTimeout, ModelRole


async def send_ama(service: AgentService) -> tuple[str, list]:
    events = await collect(service.chat("usr_ramesh", "Ama lai 20 hajar pathau"))
    return events[0].data["thread_id"], events


async def test_read_intent_never_proposes(service: AgentService, sandbox: Engine) -> None:
    n0 = transfer_count(sandbox)
    events = await collect(service.chat("usr_priya", "how much money do I have"))
    kinds = types(events)
    assert kinds[0] == "intent" and kinds[-1] == "message"
    assert "confirm_request" not in kinds and "executed" not in kinds
    assert transfer_count(sandbox) == n0


async def test_event_sequence_is_monotonic_per_trace(service: AgentService) -> None:
    _, events = await send_ama(service)
    assert {e.trace_id for e in events} and len({e.trace_id for e in events}) == 1
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    order = types(events)
    for earlier, later in [
        ("intent", "plan"), ("plan", "route"), ("route", "policy_decision"),
        ("policy_decision", "confirm_request"),
    ]:  # fmt: skip
        assert order.index(earlier) < order.index(later)
    assert order[-1] == "confirm_request"


async def test_send_money_pauses_then_executes_on_matching_hash(
    service: AgentService, sandbox: Engine
) -> None:
    before, n0 = balance(sandbox, "acc_ramesh_qar"), transfer_count(sandbox)
    thread, events = await send_ama(service)
    assert transfer_count(sandbox) == n0  # nothing moved yet
    req = next(e for e in events if e.type is ET.CONFIRM_REQUEST)
    assert balance(sandbox, "acc_ramesh_qar") == before
    done = await collect(
        service.confirm(
            "usr_ramesh",
            thread,
            req.data["proposal_hash"],
            await code_for(service, "usr_ramesh", thread),
        )
    )
    assert "executed" in types(done)
    assert balance(sandbox, "acc_ramesh_qar") < before
    assert transfer_count(sandbox) == n0 + 1


async def test_wrong_hash_fails_closed_and_nothing_moves(
    service: AgentService, sandbox: Engine
) -> None:
    before = balance(sandbox, "acc_ramesh_qar")
    thread, _ = await send_ama(service)
    done = await collect(service.confirm("usr_ramesh", thread, "0" * 64))
    kinds = types(done)
    assert "rejected" in kinds and "executed" not in kinds
    assert next(e for e in done if e.type is ET.REJECTED).data["reason"] == "hash_mismatch"
    assert balance(sandbox, "acc_ramesh_qar") == before


async def test_missing_hash_fails_closed(service: AgentService, sandbox: Engine) -> None:
    before = balance(sandbox, "acc_ramesh_qar")
    thread, _ = await send_ama(service)
    done = await collect(service.confirm("usr_ramesh", thread, None))
    assert "executed" not in types(done) and balance(sandbox, "acc_ramesh_qar") == before


async def test_confirm_twice_does_not_pay_twice(service: AgentService, sandbox: Engine) -> None:
    thread, events = await send_ama(service)
    h = next(e for e in events if e.type is ET.CONFIRM_REQUEST).data["proposal_hash"]
    await collect(
        service.confirm("usr_ramesh", thread, h, await code_for(service, "usr_ramesh", thread))
    )
    after_first = balance(sandbox, "acc_ramesh_qar")
    with pytest.raises(NoPendingConfirmation):
        await collect(
            service.confirm("usr_ramesh", thread, h, await code_for(service, "usr_ramesh", thread))
        )
    assert balance(sandbox, "acc_ramesh_qar") == after_first


async def test_cancel_discards_the_pending_proposal(service: AgentService, sandbox: Engine) -> None:
    before = balance(sandbox, "acc_ramesh_qar")
    thread, _ = await send_ama(service)
    done = await collect(service.cancel("usr_ramesh", thread))
    assert "executed" not in types(done) and balance(sandbox, "acc_ramesh_qar") == before
    with pytest.raises(NoPendingConfirmation):
        await collect(service.confirm("usr_ramesh", thread, "x"))


async def test_new_message_discards_pending_proposal(
    service: AgentService, sandbox: Engine
) -> None:
    before = balance(sandbox, "acc_ramesh_qar")
    thread, _ = await send_ama(service)
    await collect(service.chat("usr_ramesh", "how much money do I have", thread))
    assert balance(sandbox, "acc_ramesh_qar") == before


async def test_other_user_cannot_touch_a_thread(service: AgentService, sandbox: Engine) -> None:
    thread, events = await send_ama(service)
    h = next(e for e in events if e.type is ET.CONFIRM_REQUEST).data["proposal_hash"]
    for call in (
        service.confirm("usr_priya", thread, h),
        service.cancel("usr_priya", thread),
        service.chat("usr_priya", "hello", thread),
    ):
        with pytest.raises(ThreadNotFound):
            await collect(call)


async def test_an_unknown_document_id_is_refused_and_no_model_runs(
    service: AgentService, gateway: ScriptedGateway
) -> None:
    events = await collect(
        service.chat("usr_priya", "pay this invoice", None, [{"doc_id": "a" * 64}])
    )
    assert (
        "not find that document"
        in next(e for e in events if e.type is ET.MESSAGE).data["text"].lower()
    )
    assert gateway.calls == []  # not even the parser saw the turn


async def test_planner_timeout_fails_closed(
    service: AgentService, gateway: ScriptedGateway, sandbox: Engine
) -> None:
    gateway.planner_override = LLMTimeout("slow")
    before = balance(sandbox, "acc_ramesh_qar")
    events = await collect(service.chat("usr_ramesh", "Ama lai 20 hajar pathau"))
    assert "confirm_request" not in types(events) and "executed" not in types(events)
    assert balance(sandbox, "acc_ramesh_qar") == before


async def test_unknown_payee_name_asks_who(service: AgentService, sandbox: Engine) -> None:
    events = await collect(service.chat("usr_ramesh", "Bob lai 5 hajar pathau"))
    clarify = next(e for e in events if e.type is ET.CLARIFY)
    assert clarify.data["slot"] == "payee_ref" and "bob" in clarify.data["question"].lower()
    assert "confirm_request" not in types(events)


async def test_payee_of_another_user_is_not_resolvable(service: AgentService) -> None:
    events = await collect(service.chat("usr_ramesh", "Baba lai 5 hajar pathau"))  # Priya's payee
    assert next(e for e in events if e.type is ET.CLARIFY).data["slot"] == "payee_ref"
    assert "confirm_request" not in types(events)


async def test_planner_cannot_choose_another_payee(
    service: AgentService, gateway: ScriptedGateway, sandbox: Engine
) -> None:
    from corridor.contracts.llm_outputs import PlannerOutput

    gateway.planner_override = lambda ctx: PlannerOutput.model_validate(
        {
            "steps": [],
            "proposal": {
                "action": "transfer",
                "source_account_ref": "acc_ramesh_qar",
                "payee_ref": "pay_priya_parents",  # a payee the planner invented or stole
                "rationale": "attack",
            },
        }
    )
    events = await collect(service.chat("usr_ramesh", "Ama lai 20 hajar pathau"))
    assert "confirm_request" not in types(events)


async def test_proposal_provenance_comes_from_code(service: AgentService) -> None:
    thread, _ = await send_ama(service)
    proposal = ActionProposal.model_validate(
        (await service.state("usr_ramesh", thread))["proposal"]
    )
    assert proposal.payee_id is not None and proposal.payee_id.provenance.value == "system"
    assert proposal.source_account_id.provenance.value == "system"
    assert proposal.amount is not None and proposal.amount.provenance.value == "user_typed"
    assert proposal.payee_id.value == "pay_ramesh_ama"


async def test_conditional_send_blocked_when_condition_fails(
    service: AgentService, sandbox: Engine
) -> None:
    from sqlalchemy import text

    with sandbox.begin() as c:  # leave too little to cover the rent after sending
        c.execute(text("UPDATE accounts SET balance_minor = 90000 WHERE id = 'acc_ramesh_qar'"))
    events = await collect(
        service.chat("usr_ramesh", "Ama lai 20 hajar pathau, rent pugne bhaye matra")
    )
    kinds = types(events)
    assert "confirm_request" not in kinds and "executed" not in kinds


async def test_composer_role_is_used_for_reads(
    service: AgentService, gateway: ScriptedGateway
) -> None:
    await collect(service.chat("usr_priya", "how much money do I have"))
    assert ModelRole.COMPOSER in gateway.roles()
