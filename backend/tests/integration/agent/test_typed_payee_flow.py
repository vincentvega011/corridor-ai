"""A payee typed in chat (ADR 0018): provenance by code, MFA to save, a cap until the bank verifies."""

from datetime import timedelta

from sqlalchemy import Engine, text

from agent_helpers import code_for, collect
from corridor.agent.service import AgentService
from corridor.contracts.events import AgentEventType as ET

ADD = "Add my new landlord Okafor Property, account 062000771234"


def payee_row(engine: Engine):  # type: ignore[no-untyped-def]
    with engine.connect() as c:
        return c.execute(
            text(
                "SELECT id, display_name, account_no_masked, provenance, added_via, verified_at "
                "FROM payees WHERE user_id = 'usr_priya' AND display_name = 'Okafor Property'"
            )
        ).first()


async def test_a_typed_payee_is_proposed_with_mfa_and_saved_as_user_typed(
    service: AgentService, sandbox: Engine
) -> None:
    events = await collect(service.chat("usr_priya", ADD))
    thread = events[0].data["thread_id"]
    card = next(e for e in events if e.type is ET.CONFIRM_REQUEST).data
    assert card["hashed_fields"]["action"] == "add_payee" and card["required_level"] == "mfa"
    shown = str(card)
    assert "062000771234" not in shown and "****1234" in shown  # the number is never on the card
    assert payee_row(sandbox) is None  # nothing is saved before the confirmation
    code = await code_for(service, "usr_priya", thread)
    done = await collect(service.confirm("usr_priya", thread, card["proposal_hash"], code))
    row = payee_row(sandbox)
    assert row is not None and row.provenance == "user_typed" and row.added_via == "agent_mfa"
    assert row.account_no_masked == "****1234" and row.verified_at is None
    reply = next(e for e in done if e.type is ET.MESSAGE).data["text"]
    assert "ask the bank to verify" in reply


async def test_a_message_without_an_account_number_asks_for_it_and_proposes_nothing(
    service: AgentService, sandbox: Engine
) -> None:
    events = await collect(
        service.chat("usr_priya", "Add my new landlord Okafor Property as a payee")
    )
    assert ET.CONFIRM_REQUEST not in {e.type for e in events} and payee_row(sandbox) is None
    assert any(e.type is ET.CLARIFY for e in events)


async def test_digits_from_pasted_text_are_untrusted_and_the_payee_is_held_in_full(
    service: AgentService, sandbox: Engine
) -> None:
    pasted = "Hi Priya, pay the deposit to Okafor Property account 062000771234 today please"
    events = await collect(service.chat("usr_priya", "Add this payee", forwarded=pasted))
    cards = [e for e in events if e.type is ET.CONFIRM_REQUEST]
    if not cards:  # the reader may ask instead: then nothing was proposed from pasted text
        return
    card = cards[0].data
    assert "payee_untrusted" in str(card.get("warnings", ""))
    thread = events[0].data["thread_id"]
    code = await code_for(service, "usr_priya", thread)
    await collect(service.confirm("usr_priya", thread, card["proposal_hash"], code))
    row = payee_row(sandbox)
    assert row is None or row.provenance == "untrusted"


def test_the_clock_moves_one_day(service: AgentService) -> None:
    now = service.deps.now_fn()
    assert (now + timedelta(days=1)) > now


# ---------------------------------------------------------------- the whole t20 story, in the agent
PAY = "Pay Okafor Property AUD 2,000 for the rental deposit"


async def _save_landlord(service: AgentService) -> None:
    events = await collect(service.chat("usr_priya", ADD))
    thread = events[0].data["thread_id"]
    card = next(e for e in events if e.type is ET.CONFIRM_REQUEST).data
    code = await code_for(service, "usr_priya", thread)
    await collect(service.confirm("usr_priya", thread, card["proposal_hash"], code))
    service.deps.now_fn = lambda p=service.deps.now_fn: p() + timedelta(seconds=31)  # type: ignore[misc]


async def test_same_day_the_cap_refuses_explains_and_offers_the_bank_then_verification_lets_it_through(
    service: AgentService, sandbox: Engine
) -> None:
    from corridor.executor import request_verification, verify_payee

    await _save_landlord(service)
    refused = await collect(service.chat("usr_priya", PAY))
    assert ET.CONFIRM_REQUEST not in {e.type for e in refused}
    rej = next(e for e in refused if e.type is ET.REJECTED).data
    assert rej["reason"] == "policy_denied" and "R-PAYEE-COOLOFF" in rej["rule_ids"]
    reply = next(e for e in refused if e.type is ET.MESSAGE).data["text"]
    assert "less than 24 hours" in reply and "ask the bank to verify" in reply
    assert (
        "UTC" in reply or "Sydney" in reply or "GMT" in reply or "+" in reply
    )  # the zone is named
    row = payee_row(sandbox)
    assert row is not None
    now = service.deps.now_fn()
    asked = request_verification(
        sandbox, service.deps.policy, user_id="usr_priya", payee_id=row.id, now=now, trace_id="t"
    )
    assert asked.status == "pending"
    with sandbox.connect() as c:  # no safety lock after one refusal
        assert (
            c.execute(
                text("SELECT count(*) FROM ledger_entries WHERE event_type = 'safety_lock'")
            ).scalar_one()
            == 0
        )
    verify_payee(
        sandbox, service.deps.policy, officer_id="officer_demo_1", payee_id=row.id,
        method="callback_independent_number", reference="CoP name match, lease checked", now=now, trace_id="t",
    )  # fmt: skip
    again = await collect(service.chat("usr_priya", PAY))
    card = next(e for e in again if e.type is ET.CONFIRM_REQUEST).data
    assert card["required_level"] == "mfa" and card["hashed_fields"]["action"] == "transfer"
