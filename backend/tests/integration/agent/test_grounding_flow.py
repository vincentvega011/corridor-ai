"""The grounding verifier inside the agent: pass, regenerate once, then a template from facts."""

from sqlalchemy import Engine

from agent_fakes import ScriptedGateway
from agent_helpers import collect
from corridor.agent.service import AgentService
from corridor.contracts.events import AgentEventType as ET
from corridor.contracts.llm_outputs import ComposerOutput
from corridor.llm import ModelRole


def grounding(events):  # type: ignore[no-untyped-def]
    return next(e for e in events if e.type is ET.GROUNDING).data


def final_text(events) -> str:  # type: ignore[no-untyped-def]
    return next(e for e in events if e.type is ET.MESSAGE).data["text"]


async def test_a_grounded_composer_reply_passes(service: AgentService) -> None:
    gw: ScriptedGateway = service.deps.gateway  # type: ignore[assignment]
    gw.composer_override = ComposerOutput(reply="Your AUD balance is AUD 4,200.00.")
    events = await collect(service.chat("usr_priya", "how much money do I have"))
    g = grounding(events)
    assert (
        g["outcome"] == "pass"
        and g["numbers_checked"] >= 1
        and "AUD 4,200.00" in final_text(events)
    )


async def test_a_composer_that_invents_a_number_is_regenerated_then_templated(
    service: AgentService, sandbox: Engine
) -> None:
    gw: ScriptedGateway = service.deps.gateway  # type: ignore[assignment]
    gw.composer_override = ComposerOutput(
        reply="You have AUD 9,999.00, and next week it will be AUD 10,500.00."
    )
    events = await collect(service.chat("usr_priya", "how much money do I have"))
    g = grounding(events)
    assert (
        g["outcome"] == "templated"
        and len(g["attempts"]) == 2
        and all(not a["ok"] for a in g["attempts"])
    )
    text = final_text(events)
    assert "9,999" not in text and "10,500" not in text and "AUD 4,200.00" in text  # facts only
    assert sum(1 for role, _ in gw.calls if role is ModelRole.COMPOSER) == 2  # exactly one retry
    strict = [m["content"] for role, ms in gw.calls if role is ModelRole.COMPOSER for m in ms][-1]
    assert "strict_instruction" in strict and "9,999.00" in strict
    from sqlalchemy import text as sql

    with sandbox.connect() as c:
        kinds = [r[0] for r in c.execute(sql("SELECT event_type FROM ledger_entries"))]
    assert "grounding_failure" in kinds  # both attempts are logged


async def test_a_regeneration_that_is_grounded_is_used(service: AgentService) -> None:
    gw: ScriptedGateway = service.deps.gateway  # type: ignore[assignment]
    replies = iter(["You will have AUD 1.00 next year", "Your AUD balance is AUD 4,200.00."])
    gw.composer_override = None
    original = gw.complete

    async def scripted(role, messages, schema=None, tools=None, **kw):  # type: ignore[no-untyped-def]
        if role is ModelRole.COMPOSER:
            gw.calls.append((role, messages))
            from agent_fakes import FakeResult

            return FakeResult(ComposerOutput(reply=next(replies)))
        return await original(role, messages, schema, tools, **kw)

    gw.complete = scripted  # type: ignore[method-assign]
    events = await collect(service.chat("usr_priya", "how much money do I have"))
    assert grounding(events)["outcome"] == "regenerated" and "4,200.00" in final_text(events)


async def test_hallucination_bait_never_yields_an_ungrounded_number(service: AgentService) -> None:
    gw: ScriptedGateway = service.deps.gateway  # type: ignore[assignment]
    bait = [
        (
            "how much money do I have, and what will the rate be next week exactly?",
            "Next week 1000 AUD will be exactly NPR 91,234.50 at 91.2345.",
        ),
        (
            "how much money do I have, and how much will I save next year?",
            "You have AUD 4,200.00 and will save NPR 45,000.00 next year.",
        ),
    ]
    from corridor.grounding import verify_reply

    for question, lie in bait:
        gw.composer_override = ComposerOutput(reply=lie)
        events = await collect(service.chat("usr_priya", question))
        shown = [e for e in events if e.type is ET.MESSAGE]
        assert shown and any(e.type is ET.GROUNDING for e in events), (
            question,
            [e.type for e in events],
        )
        g = grounding(events)
        if g["outcome"] != "pass":
            assert g["outcome"] == "templated"
            assert "45,000" not in shown[0].data["text"] and "91,234" not in shown[0].data["text"]
    assert verify_reply is not None


async def test_the_executed_receipt_is_grounded_in_the_executed_amounts(
    service: AgentService, sandbox: Engine
) -> None:
    from agent_helpers import code_for

    events = await collect(service.chat("usr_ramesh", "send QAR 50 to my landlord"))
    thread = events[0].data["thread_id"]
    h = next(e for e in events if e.type is ET.CONFIRM_REQUEST).data["proposal_hash"]
    done = await collect(
        service.confirm("usr_ramesh", thread, h, await code_for(service, "usr_ramesh", thread))
    )
    assert grounding(done)["outcome"] == "pass" and "QAR 50.00" in final_text(done)
