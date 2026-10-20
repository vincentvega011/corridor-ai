"""When no model can answer, ONE honest message in the user's language names the buttons that still work (P4-2)."""

from __future__ import annotations

import pytest

from agent_helpers import collect, transfer_count
from corridor.agent.service import AgentService
from corridor.contracts.events import AgentEventType as ET
from corridor.llm import CassetteMiss, LLMConfigError, LLMTimeout, LLMUnavailable, QuotaExhausted

CASES = [
    ("usr_priya", "Pay my tuition invoice before the due date", "Credit Passport"),
    ("usr_ramesh", "Ama lai 20 hajar rupiya pathau", "Credit Passport"),
    ("usr_ramesh", "आमालाई २० हजार रुपैयाँ पठाउ", "क्रेडिट पासपोर्ट"),
]
FAILURES = [
    LLMTimeout("slow"),
    LLMUnavailable("down", status=503),
    QuotaExhausted("tokenharbor", 3600),
]


def fail_with(exc: Exception):  # type: ignore[no-untyped-def]
    def raiser(ctx: object) -> object:
        raise exc

    return raiser


@pytest.mark.parametrize("exc", FAILURES)
@pytest.mark.parametrize(("user", "text", "needle"), CASES)
async def test_a_planner_failure_gives_exactly_one_message_and_moves_nothing(
    service: AgentService, sandbox, user: str, text: str, needle: str, exc: Exception
) -> None:
    service.deps.gateway.planner_override = fail_with(exc)  # type: ignore[attr-defined]
    n0 = transfer_count(sandbox)
    events = await collect(service.chat(user, text))
    messages = [e for e in events if e.type is ET.MESSAGE]
    assert len(messages) == 1 and needle in messages[0].data["text"]
    assert not [e for e in events if e.type is ET.ERROR]  # no second, generic error line
    assert not [e for e in events if e.type is ET.CONFIRM_REQUEST]
    assert transfer_count(sandbox) == n0


async def test_a_parser_that_cannot_answer_is_unavailable_not_rephrase(
    service: AgentService,
) -> None:
    service.deps.gateway.parser_override = CassetteMiss("k", "parser")  # type: ignore[attr-defined]
    events = await collect(service.chat("usr_priya", "hello there banana"))
    messages = [e.data["text"] for e in events if e.type is ET.MESSAGE]
    assert (
        len(messages) == 1
        and "cannot answer" in messages[0]
        and "rephrase" not in messages[0].lower()
    )


async def test_an_unsure_local_parser_whose_cloud_fallback_is_missing_is_unavailable_not_rephrase(
    service: AgentService, sandbox
) -> None:
    """Offline replay or no key: the local parser disagrees with the rules (low verified confidence), the cloud parser cannot answer."""
    from corridor.contracts.llm_outputs import ParserOutput

    calls = {"n": 0}

    def parser(text: str) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            return ParserOutput(
                language="en", intent="check_balance", slots={}, confidence_bps=9000
            )  # type: ignore[arg-type]
        return LLMConfigError("no key")

    service.deps.gateway.parser_override = parser  # type: ignore[attr-defined]
    n0 = transfer_count(sandbox)
    events = await collect(service.chat("usr_priya", "Please pay this deposit for the room"))
    messages = [e.data["text"] for e in events if e.type is ET.MESSAGE]
    assert len(messages) == 1 and "cannot answer" in messages[0]
    assert "rephrase" not in messages[0].lower() and "did not understand" not in messages[0].lower()
    assert transfer_count(sandbox) == n0
