"""The multi step funding plan and the passport requests through the agent graph (P3-1, ADRs 0014, 0015)."""

import json
from datetime import timedelta

from sqlalchemy import Engine, text

from agent_helpers import code_for, collect, types
from corridor.agent.service import AgentService
from corridor.contracts.events import AgentEventType as ET

PAY = "Pay my tuition invoice before the due date"


def card_of(events):  # type: ignore[no-untyped-def]
    return next(e for e in reversed(events) if e.type is ET.CONFIRM_REQUEST).data


def invoice(engine: Engine) -> tuple[str, int]:
    with engine.connect() as c:
        r = c.execute(
            text("SELECT status, paid_minor FROM invoices WHERE id = 'inv_priya_tuition_2026s2'")
        ).one()
    return str(r[0]), int(r[1])


async def confirm(service: AgentService, thread: str, card) -> list:  # type: ignore[no-untyped-def,type-arg]
    code = None
    if card["required_level"] == "mfa":
        code = await code_for(service, "usr_priya", thread)
        prev = (
            service.deps.now_fn
        )  # the next authenticator code comes 31 seconds later (never reused)
        out = await collect(service.confirm("usr_priya", thread, card["proposal_hash"], code))
        service.deps.now_fn = lambda p=prev: p() + timedelta(seconds=31)  # type: ignore[misc]
        return out
    return await collect(service.confirm("usr_priya", thread, card["proposal_hash"], code))


async def test_the_plan_is_a_queue_of_separate_proposals_each_with_its_own_grant(
    service: AgentService, sandbox: Engine
) -> None:
    events = await collect(service.chat("usr_priya", PAY))
    thread = events[0].data["thread_id"]
    route = next(e for e in events if e.type is ET.ROUTE).data
    assert route["label"] == "funding_plan" and [s["type"] for s in route["steps"]] == [
        "request_contribution", "pay_invoice", "pay_invoice",
    ]  # fmt: skip
    seen = []
    for expected in ("request_contribution", "pay_invoice", "pay_invoice"):
        card = card_of(events)
        assert card["hashed_fields"]["action"] == expected and card["plan_step"]["total"] == 3
        seen.append(card["proposal_hash"])
        events = await confirm(service, thread, card)
        if expected != "pay_invoice" or len(seen) < 3:
            assert "confirm_request" in types(events)  # the next step has its own card
    assert len(set(seen)) == 3  # three different hashes, three different grants
    assert invoice(sandbox) == ("part_paid", 520_000)
    reply = next(e for e in events if e.type is ET.MESSAGE).data["text"]
    assert "AUD 1,300.00" in reply  # what is still to pay once the family money arrives
    with sandbox.connect() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM contribution_requests WHERE status = 'pending'")
            ).scalar_one()
            == 1
        )
        assert (
            c.execute(
                text(
                    "SELECT count(*) FROM transfers WHERE user_id = 'usr_priya' AND created_at > now() - interval '1 hour'"
                )
            ).scalar_one()
            == 2
        )


async def test_cancelling_midway_drops_the_rest_of_the_plan_and_a_wrong_hash_moves_nothing(
    service: AgentService, sandbox: Engine
) -> None:
    first_try = await collect(service.chat("usr_priya", PAY))
    bad = await collect(
        service.confirm("usr_priya", first_try[0].data["thread_id"], "f" * 64, None)
    )
    assert any(e.type is ET.REJECTED and e.data["reason"] == "hash_mismatch" for e in bad)
    assert invoice(sandbox) == ("open", 0)  # a confirmation of some other hash moved nothing
    with sandbox.connect() as c:
        assert c.execute(text("SELECT count(*) FROM contribution_requests")).scalar_one() == 0
    events = await collect(service.chat("usr_priya", PAY))
    thread = events[0].data["thread_id"]
    first = card_of(events)
    events = await confirm(service, thread, first)  # the request is stored, the next card comes
    assert card_of(events)["hashed_fields"]["action"] == "pay_invoice"
    await collect(service.cancel("usr_priya", thread))
    assert invoice(sandbox) == ("open", 0)  # the payments were never made
    with sandbox.connect() as c:
        assert c.execute(text("SELECT count(*) FROM contribution_requests")).scalar_one() == 1


async def test_an_infeasible_plan_says_so_and_proposes_nothing(
    service: AgentService, sandbox: Engine
) -> None:
    with sandbox.begin() as c:
        c.execute(text("UPDATE accounts SET balance_minor = 100000 WHERE id = 'acc_priya_aud'"))
        c.execute(text("UPDATE accounts SET balance_minor = 0 WHERE id = 'acc_priya_aud_savings'"))
    events = await collect(service.chat("usr_priya", PAY))
    assert "confirm_request" not in types(events)
    assert any(e.type is ET.REJECTED and e.data["reason"] == "plan_infeasible" for e in events)
    assert invoice(sandbox) == ("open", 0)


async def test_issue_then_share_only_the_rent_claim(service: AgentService, sandbox: Engine) -> None:
    events = await collect(service.chat("usr_priya", "Please issue my credit passport"))
    thread = events[0].data["thread_id"]
    done = await confirm(service, thread, card_of(events))
    assert any(e.type is ET.EXECUTED and e.data["action"] == "issue_passport" for e in done)

    ask = await collect(service.chat("usr_priya", "share my passport with a landlord"))
    assert any(
        e.type is ET.CLARIFY and e.data["slot"] == "passport_claim" for e in ask
    )  # which proof? never guessed

    events = await collect(
        service.chat("usr_priya", "Share proof that I pay rent on time with a new landlord")
    )
    t2 = events[0].data["thread_id"]
    card = card_of(events)
    assert card["required_level"] == "mfa" and card["display"]["for"] == "new landlord"
    assert card["display"]["shares_only"] == ["rent payments made on time (count)"]
    shared = await confirm(service, t2, card)
    ex = next(e for e in shared if e.type is ET.EXECUTED).data
    assert [d["claim"]["id"] for d in ex["presentation"]["disclosures"]] == ["rent_on_time"]
    assert "amount" not in json.dumps(ex["presentation"]["disclosures"])


async def test_sharing_without_a_passport_is_refused_in_words(service: AgentService) -> None:
    events = await collect(
        service.chat("usr_priya", "Share proof that I pay rent on time with a new landlord")
    )
    assert "confirm_request" not in types(events)
    assert any(e.type is ET.REJECTED and e.data["reason"] == "no_passport" for e in events)
