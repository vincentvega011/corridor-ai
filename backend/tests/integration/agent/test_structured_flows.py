"""Button flows are deterministic: no parser, no planner, the same proposals as chat (ADR 0021)."""

from __future__ import annotations

from typing import Any

from agent_helpers import collect
from corridor.agent.service import AgentService
from corridor.contracts.events import AgentEventType as ET
from corridor.llm import ModelRole


def cards(events: list[Any]) -> list[dict[str, Any]]:
    return [e.data for e in events if e.type is ET.CONFIRM_REQUEST]


def comparable(card: dict[str, Any]) -> dict[str, Any]:
    """The proposal without what is random by design (the idempotency key and the hash made from it)."""
    hashed = {k: v for k, v in card["hashed_fields"].items() if k != "idempotency_key"}
    return {
        "hashed": hashed,
        "display": card["display"],
        "route": card.get("route"),
        "level": card["required_level"],
        "rules": card["rule_ids"],
    }


async def test_a_payment_button_makes_the_same_proposal_as_the_chat_without_any_model(
    service: AgentService,
) -> None:
    chat = await collect(service.chat("usr_ramesh", "Ama lai 20 hajar rupiya pathau"))
    gw = service.deps.gateway
    chat_roles = {r for r, _ in gw.calls}  # type: ignore[attr-defined]
    gw.calls.clear()  # type: ignore[attr-defined]
    action = {
        "kind": "send_money",
        "payee_id": "pay_ramesh_ama",
        "amount_minor": 2000000,
        "currency": "NPR",
        "language": "en",
    }
    button = await collect(service.chat("usr_ramesh", "", structured=action))
    assert ModelRole.PARSER in chat_roles and ModelRole.PLANNER in chat_roles
    assert gw.calls == []  # type: ignore[attr-defined]  # no parser, no planner, no composer
    assert len(cards(chat)) == len(cards(button)) == 1
    assert comparable(cards(button)[0]) == comparable(cards(chat)[0])
    assert next(e for e in button if e.type is ET.INTENT).data["parser"] == "structured"


async def test_the_tuition_button_makes_the_same_first_card_as_the_chat_without_any_model(
    service: AgentService,
) -> None:
    chat = await collect(service.chat("usr_priya", "Pay my tuition invoice before the due date"))
    gw = service.deps.gateway
    gw.calls.clear()  # type: ignore[attr-defined]
    action = {"kind": "pay_invoice", "invoice_id": "inv_priya_tuition_2026s2", "language": "en"}
    button = await collect(service.chat("usr_priya", "", structured=action))
    assert gw.calls == []  # type: ignore[attr-defined]
    assert cards(chat) and cards(button)
    assert comparable(cards(button)[0]) == comparable(cards(chat)[0])
    steps = [e.data for e in button if e.type is ET.ROUTE and e.data.get("label") == "funding_plan"]
    assert steps  # the plan made by code (CP-SAT), shown step by step


async def test_a_button_cannot_name_another_users_payee_or_invoice(service: AgentService) -> None:
    act = {
        "kind": "send_money",
        "payee_id": "pay_priya_landlord",
        "amount_minor": 1000,
        "currency": "AUD",
        "language": "en",
    }
    ev = await collect(service.chat("usr_ramesh", "", structured=act))
    assert not cards(ev)  # Ramesh has no such payee
    act2 = {"kind": "pay_invoice", "invoice_id": "inv_priya_tuition_2026s2", "language": "en"}
    ev2 = await collect(service.chat("usr_ramesh", "", structured=act2))
    assert not cards(ev2)


async def test_a_button_turn_does_not_leak_into_the_next_typed_turn_of_the_same_thread(
    service: AgentService,
) -> None:
    action = {
        "kind": "send_money",
        "payee_id": "pay_ramesh_ama",
        "amount_minor": 2000000,
        "currency": "NPR",
        "language": "en",
    }
    first = await collect(service.chat("usr_ramesh", "", structured=action))
    thread = first[0].data["thread_id"]
    second = await collect(service.chat("usr_ramesh", "how much money do I have", thread_id=thread))
    intent = next(e for e in second if e.type is ET.INTENT).data
    assert intent["parser"] != "structured" and intent["intent"] == "check_balance"
    assert not cards(second)  # no stale payment card for a balance question
