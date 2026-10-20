"""The real policy, grants, MFA, safety lock and ledger wired into the agent graph (P2-1)."""

import json
import re
from datetime import timedelta

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from agent_helpers import balance, code_for, collect, types
from corridor.agent.service import AgentService
from corridor.confirm import current_code, lock_user
from corridor.contracts.events import AgentEventType as ET
from corridor.ledger import verify_chain
from corridor.sandbox import parser_trace


def decision_of(events):  # type: ignore[no-untyped-def]
    return next(e for e in events if e.type is ET.POLICY_DECISION).data


def ledger_kinds(engine: Engine) -> list[str]:
    with engine.connect() as c:
        return [r[0] for r in c.execute(text("SELECT event_type FROM ledger_entries ORDER BY seq"))]


async def test_small_payment_to_a_verified_payee_needs_only_a_confirmation(
    service: AgentService, sandbox: Engine
) -> None:
    events = await collect(service.chat("usr_ramesh", "send QAR 50 to my landlord"))
    d = decision_of(events)
    assert d["decision"] == "require_confirm" and d["required_level"] == "confirm"
    assert "R-TIER-LOW" in d["rule_ids"] and d["reasons"] and d["policy_version_hash"]
    thread = events[0].data["thread_id"]
    h = next(e for e in events if e.type is ET.CONFIRM_REQUEST).data["proposal_hash"]
    done = await collect(service.confirm("usr_ramesh", thread, h))  # no code needed
    assert "executed" in types(done)


async def test_large_payment_needs_mfa_and_cannot_run_without_a_code(
    service: AgentService, sandbox: Engine
) -> None:
    events = await collect(service.chat("usr_ramesh", "Ama lai 40 hajar pathau"))
    d = decision_of(events)
    assert d["decision"] == "require_mfa" and d["required_level"] == "mfa"
    assert "R-TIER-HIGH" in d["rule_ids"] and any(
        "authenticator" in r.lower() or "code" in r.lower() for r in d["reasons"]
    )
    thread = events[0].data["thread_id"]
    card = next(e for e in events if e.type is ET.CONFIRM_REQUEST).data
    before = balance(sandbox, "acc_ramesh_qar")
    refused = await collect(service.confirm("usr_ramesh", thread, card["proposal_hash"]))
    assert next(e for e in refused if e.type is ET.REJECTED).data["reason"] == "mfa_required"
    assert balance(sandbox, "acc_ramesh_qar") == before
    wrong = await collect(service.confirm("usr_ramesh", thread, card["proposal_hash"], "000001"))
    rej = next(e for e in wrong if e.type is ET.REJECTED).data
    assert rej["reason"] == "mfa_failed" and rej["attempts_left"] == 4
    seqs = [e.seq for e in refused + wrong]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    ok = await collect(
        service.confirm(
            "usr_ramesh",
            thread,
            card["proposal_hash"],
            await code_for(service, "usr_ramesh", thread),
        )
    )
    assert "executed" in types(ok) and balance(sandbox, "acc_ramesh_qar") < before


async def test_a_new_payee_needs_mfa_even_for_a_small_amount(
    service: AgentService, sandbox: Engine
) -> None:
    with sandbox.begin() as c:
        c.execute(
            text(
                "UPDATE payees SET created_at = now() - interval '2 days' WHERE id = 'pay_ramesh_landlord'"
            )
        )
    events = await collect(service.chat("usr_ramesh", "send QAR 50 to my landlord"))
    d = decision_of(events)
    assert d["decision"] == "require_mfa" and "R-TIER-HIGH" in d["rule_ids"]
    assert "R-TIER-HIGH.new_payee" in d["reason_keys"]


async def test_the_policy_reasons_come_in_the_users_language_style(service: AgentService) -> None:
    ne = decision_of(await collect(service.chat("usr_ramesh", "Ama lai 40 hajar pathau")))
    en = decision_of(await collect(service.chat("usr_ramesh", "send QAR 50 to my landlord")))
    assert any("chahinchha" in r for r in ne["reasons"])
    assert all("chahinchha" not in r for r in en["reasons"])


async def test_a_denial_is_explained_ledgered_and_nothing_moves(
    service: AgentService, sandbox: Engine
) -> None:
    with Session(sandbox) as s, s.begin():
        lock_user(s, "usr_ramesh", "manual", service.deps.now_fn())
    before = balance(sandbox, "acc_ramesh_qar")
    events = await collect(service.chat("usr_ramesh", "send QAR 50 to my landlord"))
    d = decision_of(events)
    assert d["decision"] == "deny" and d["rule_ids"][0] == "R-LOCK"
    assert "confirm_request" not in types(events)
    assert next(e for e in events if e.type is ET.REJECTED).data["reason"] == "policy_denied"
    assert balance(sandbox, "acc_ramesh_qar") == before
    assert {"proposal", "policy_decision"} <= set(ledger_kinds(sandbox))


async def test_every_step_of_a_payment_is_in_the_ledger_before_the_reply(
    service: AgentService, sandbox: Engine
) -> None:
    events = await collect(service.chat("usr_ramesh", "Ama lai 40 hajar pathau"))
    appended = [e.data["event_type"] for e in events if e.type is ET.LEDGER_APPEND]
    assert appended == [
        "cascade_decision",
        "proposal",
        "policy_decision",
    ]  # all written before the confirm card
    assert types(events).index("ledger_append") < types(events).index("confirm_request")
    thread = events[0].data["thread_id"]
    h = next(e for e in events if e.type is ET.CONFIRM_REQUEST).data["proposal_hash"]
    await collect(
        service.confirm("usr_ramesh", thread, h, await code_for(service, "usr_ramesh", thread))
    )
    kinds = ledger_kinds(sandbox)
    assert kinds == [
        "cascade_decision",
        "proposal",
        "policy_decision",
        "mfa_attempt",
        "confirmation_granted",
        "execution",
    ]
    with Session(sandbox) as s:
        assert verify_chain(s).valid


async def test_cancel_and_hash_mismatch_are_ledgered(
    service: AgentService, sandbox: Engine
) -> None:
    events = await collect(service.chat("usr_ramesh", "send QAR 50 to my landlord"))
    thread = events[0].data["thread_id"]
    await collect(service.confirm("usr_ramesh", thread, "0" * 64))
    assert "confirmation_rejected" in ledger_kinds(sandbox)
    events = await collect(service.chat("usr_ramesh", "send QAR 50 to my landlord"))
    await collect(service.cancel("usr_ramesh", events[0].data["thread_id"]))
    assert "proposal_discarded" in ledger_kinds(sandbox)


async def test_five_wrong_codes_lock_and_the_unlock_needs_a_code(
    service: AgentService, sandbox: Engine
) -> None:
    events = await collect(service.chat("usr_ramesh", "Ama lai 40 hajar pathau"))
    thread = events[0].data["thread_id"]
    h = next(e for e in events if e.type is ET.CONFIRM_REQUEST).data["proposal_hash"]
    last = []
    for _ in range(5):
        last = await collect(service.confirm("usr_ramesh", thread, h, "000001"))
    assert next(e for e in last if e.type is ET.REJECTED).data["locked"] is True
    again = await collect(
        service.confirm("usr_ramesh", thread, h, await code_for(service, "usr_ramesh", thread))
    )
    assert next(e for e in again if e.type is ET.REJECTED).data["reason"] == "locked"
    assert (await service.unlock("usr_ramesh", "000002")).ok is False
    now = service.deps.now_fn()
    code = current_code("usr_ramesh", now, service.deps.secrets_dir)
    cooling = await service.unlock(
        "usr_ramesh", code
    )  # 5 wrong codes: a cooldown, even for the right one
    assert cooling.ok is False and cooling.reason == "unlock_cooldown"
    from datetime import timedelta

    later = now + timedelta(minutes=11)
    service.deps.now_fn = lambda: later  # type: ignore[method-assign]
    good = await service.unlock(
        "usr_ramesh", current_code("usr_ramesh", later, service.deps.secrets_dir)
    )
    assert good.ok
    service.deps.now_fn = lambda: later + timedelta(seconds=35)  # type: ignore[method-assign]
    ok = await collect(
        service.confirm(
            "usr_ramesh",
            thread,
            h,
            current_code("usr_ramesh", service.deps.now_fn(), service.deps.secrets_dir),
        )
    )
    assert "executed" in types(ok)


async def test_three_confirmations_for_the_wrong_hash_lock_the_user(
    service: AgentService, sandbox: Engine
) -> None:
    for _ in range(3):
        events = await collect(service.chat("usr_ramesh", "send QAR 50 to my landlord"))
        await collect(service.confirm("usr_ramesh", events[0].data["thread_id"], "0" * 64))
    with sandbox.connect() as c:
        locked = c.execute(
            text("SELECT locked FROM safety_locks WHERE user_id = 'usr_ramesh'")
        ).scalar_one()
    assert locked and "safety_lock" in ledger_kinds(sandbox)


async def test_a_routine_family_remittance_is_confirm_only(service: AgentService) -> None:
    """NPR 20,000 to Ama (added over a year ago) is under the NPR 25,000 low tier: one tap."""
    events = await collect(service.chat("usr_ramesh", "Ama lai 20 hajar pathau"))
    d = decision_of(events)
    assert d["decision"] == "require_confirm" and "R-TIER-LOW" in d["rule_ids"]
    thread = events[0].data["thread_id"]
    h = next(e for e in events if e.type is ET.CONFIRM_REQUEST).data["proposal_hash"]
    assert "executed" in types(await collect(service.confirm("usr_ramesh", thread, h)))


async def test_the_ledger_holds_only_a_hash_of_the_parser_output_and_the_text_expires(
    service: AgentService, sandbox: Engine
) -> None:
    """The masked parser output lives in the trace store (INV-12), the hash chain holds only its reference."""
    await collect(service.chat("usr_ramesh", "Ama lai 40 hajar pathau"))
    with sandbox.connect() as c:
        payloads = [
            json.loads(r[0])
            for r in c.execute(
                text(
                    "SELECT payload_canonical_json FROM ledger_entries "
                    "WHERE event_type = 'cascade_decision'"
                )
            )
        ]
    assert payloads and "parser_output" not in payloads[0]
    ref = payloads[0]["parser_output_hash"]
    assert re.fullmatch(r"[0-9a-f]{64}", ref)
    now = service.deps.now_fn()
    with Session(sandbox) as s:
        got = parser_trace.lookup(s, [ref], now)[ref]
        assert got is not None and "intent" in got and not re.search(r"\d{6,}", got)
        later = now + timedelta(days=parser_trace.RETENTION_DAYS, seconds=1)
        assert parser_trace.lookup(s, [ref], later)[ref] is None  # expired: the console says so
    with Session(sandbox) as s:
        assert verify_chain(s).valid


async def test_a_cross_border_card_carries_the_date_of_the_rate_it_used(
    service: AgentService, sandbox: Engine
) -> None:
    """The card shows where the exchange rate comes from: the seeded NRB table and its date (P4-2)."""
    events = await collect(service.chat("usr_ramesh", "Ama lai 20 hajar pathau"))
    card = next(e for e in events if e.type is ET.CONFIRM_REQUEST)
    assert card.data["route"]["rate_date"] == "2026-10-09"
