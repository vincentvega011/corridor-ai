"""After "that date has already passed": a negation cancels, a go ahead continues, anything else asks again (P3-4c)."""

import pytest
from sqlalchemy import Engine

from agent_helpers import collect, transfer_count, types
from corridor.agent.service import AgentService
from corridor.contracts.events import AgentEventType as ET
from corridor.nlu.deadline_reply import classify

FIRST = {
    "en": "Send QAR 800 to Ama by yesterday",
    "ne_latn": "Ama lai 800 QAR hijo samma pathau",
    "ne": "आमालाई २० हजार रुपैयाँ हिजो पठाउ",
}
CANCEL = {"en": "no, cancel", "ne_latn": "hoina, napathau", "ne": "रद्द गर"}
GO = {"en": "send now", "ne_latn": "ahile pathau", "ne": "अहिले पठाऊ"}
OTHER = {"en": "banana", "ne_latn": "kei chaina", "ne": "केरा"}


def test_the_rules_for_each_language() -> None:
    for lang in FIRST:
        assert classify(CANCEL[lang]) == "cancel"
        assert classify(GO[lang]) == "affirm"
        assert classify(OTHER[lang]) is None
    assert classify("no deadline please") == "affirm"
    assert classify("without a deadline") == "affirm"


async def first(service: AgentService, lang: str) -> str:
    events = await collect(service.chat("usr_ramesh", FIRST[lang]))
    clarify = [e for e in events if e.type is ET.CLARIFY]
    assert clarify and clarify[-1].data["slot"] == "deadline_past"
    return events[0].data["thread_id"]


@pytest.mark.parametrize("lang", ["en", "ne_latn", "ne"])
async def test_a_negation_cancels_and_nothing_moves(
    service: AgentService, sandbox: Engine, lang: str
) -> None:
    n0 = transfer_count(sandbox)
    thread = await first(service, lang)
    events = await collect(service.chat("usr_ramesh", CANCEL[lang], thread_id=thread))
    assert "confirm_request" not in types(events)
    assert [e.data["reason"] for e in events if e.type is ET.REJECTED] == ["user_cancelled"]
    assert transfer_count(sandbox) == n0


@pytest.mark.parametrize("lang", ["en", "ne_latn", "ne"])
async def test_an_unrelated_reply_asks_again(service: AgentService, lang: str) -> None:
    thread = await first(service, lang)
    events = await collect(service.chat("usr_ramesh", OTHER[lang], thread_id=thread))
    again = [e for e in events if e.type is ET.CLARIFY]
    assert again and again[-1].data["slot"] == "deadline_past"
    assert "confirm_request" not in types(events)


@pytest.mark.parametrize("lang", ["en", "ne_latn", "ne"])
async def test_a_go_ahead_continues_with_no_deadline(service: AgentService, lang: str) -> None:
    thread = await first(service, lang)
    events = await collect(service.chat("usr_ramesh", GO[lang], thread_id=thread))
    assert "confirm_request" in types(events)
