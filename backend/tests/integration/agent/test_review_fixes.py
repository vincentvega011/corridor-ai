"""Regression tests for the P1 security review notes (N1 to N4)."""

import asyncio

import httpx
from sqlalchemy import Engine

from agent_fakes import ScriptedGateway
from agent_helpers import balance, code_for, collect, transfer_count, types
from corridor.agent.service import AgentService
from corridor.contracts.events import AgentEventType as ET
from corridor.contracts.llm_outputs import ParserOutput


async def test_model_inferred_currency_is_asked_not_used(
    service: AgentService, gateway: ScriptedGateway, sandbox: Engine
) -> None:
    """N1: the parser invents "AUD" for "send 20000 to Ama"; the agent must ask, never debit AUD."""
    gateway.parser_override = lambda text: ParserOutput(
        language="en", intent="send_money", confidence_bps=9000,
        slots={"currency": "AUD", "amount": 20000, "payee_ref": "Ama"},
    )  # fmt: skip
    events = await collect(service.chat("usr_ramesh", "send 20000 to Ama"))
    assert "confirm_request" not in types(events)
    assert next(e for e in events if e.type is ET.CLARIFY).data["slot"] == "currency"


async def test_confirm_and_cancel_race_executes_at_most_once(
    service: AgentService, sandbox: Engine
) -> None:
    """N2: confirm and cancel in parallel on one thread are serialised; one wins, never both."""
    events = await collect(service.chat("usr_ramesh", "Ama lai 20 hajar pathau"))
    thread = events[0].data["thread_id"]
    h = next(e for e in events if e.type is ET.CONFIRM_REQUEST).data["proposal_hash"]
    start, n0 = balance(sandbox, "acc_ramesh_qar"), transfer_count(sandbox)

    async def confirm() -> list:
        try:
            return await collect(
                service.confirm(
                    "usr_ramesh", thread, h, await code_for(service, "usr_ramesh", thread)
                )
            )
        except Exception as exc:  # NoPendingConfirmation when cancel won
            return [exc]

    results = await asyncio.gather(confirm(), collect(service.cancel("usr_ramesh", thread)))
    executed = any(getattr(e, "type", None) is ET.EXECUTED for e in results[0])
    assert transfer_count(sandbox) == n0 + (1 if executed else 0)
    assert (balance(sandbox, "acc_ramesh_qar") < start) == executed


async def test_failure_after_commit_still_reports_executed(
    service: AgentService, gateway: ScriptedGateway, sandbox: Engine, monkeypatch
) -> None:
    """N3: the receipt step blows up after the debit; the user must not be told nothing was sent."""
    events = await collect(service.chat("usr_ramesh", "Ama lai 20 hajar pathau"))
    thread = events[0].data["thread_id"]
    h = next(e for e in events if e.type is ET.CONFIRM_REQUEST).data["proposal_hash"]

    async def boom(*a, **k):
        raise RuntimeError("receipt failed")

    monkeypatch.setattr("corridor.agent.compose.compose_executed_reply", boom)
    done = await collect(
        service.confirm("usr_ramesh", thread, h, await code_for(service, "usr_ramesh", thread))
    )
    assert "executed" in types(done)
    text = next(e for e in done if e.type is ET.MESSAGE).data["text"].lower()
    assert "nothing was sent" not in text and "kehi pathaiyena" not in text
    assert (await service.state("usr_ramesh", thread))["status"] == "executed"


async def test_dev_chat_refuses_a_foreign_host_header(client: httpx.AsyncClient) -> None:
    """N4: DNS rebinding sends a loopback client address with an attacker Host header."""
    ok = await client.get("/dev/chat")
    evil = await client.get("/dev/chat", headers={"Host": "evil.example"})
    assert ok.status_code == 200 and evil.status_code == 404
    assert ok.headers["x-frame-options"] == "DENY"
