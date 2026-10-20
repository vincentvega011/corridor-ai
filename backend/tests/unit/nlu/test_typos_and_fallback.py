"""Misspelled key words still classify, and with no model at all the keyword rules still understand the request (P4-2)."""

from __future__ import annotations

from datetime import date

import pytest

from agent_fakes import ScriptedGateway
from corridor.contracts.intent import IntentType
from corridor.llm import CassetteMiss, ModelRole
from corridor.nlu.lang_pack import load_packs
from corridor.nlu.parser import parse
from corridor.nlu.rules import classify_intent

PACKS = load_packs()
TODAY = date(2026, 10, 10)

TYPOS = [
    ("pay my tutition invoice before due date", IntentType.PAY_INVOICE),
    ("pay my tution invoice", IntentType.PAY_INVOICE),
    ("please pay the invoise", IntentType.PAY_INVOICE),
    ("pay my invioce", IntentType.PAY_INVOICE),
    ("show my balence", IntentType.CHECK_BALANCE),
    ("tutition fee tirnu parne chha", IntentType.PAY_INVOICE),
    ("mero tyusan fi tirnu parne chha", IntentType.PAY_INVOICE),
    ("invoise tirdinu", IntentType.PAY_INVOICE),
    ("mero balans kati chha", IntentType.CHECK_BALANCE),
    ("naya payee save garnu", IntentType.ADD_PAYEE),
    ("naya peyi thap", IntentType.ADD_PAYEE),
]


@pytest.mark.parametrize(("text", "intent"), TYPOS)
def test_common_misspellings_still_classify(text: str, intent: IntentType) -> None:
    assert classify_intent(text, None, PACKS, TODAY).intent is intent


class NoModel(ScriptedGateway):
    """Every model call fails the way a blocked provider or a replay without a recording does."""

    async def complete(self, role: ModelRole, *a: object, **k: object):  # type: ignore[override]
        raise CassetteMiss("k", role.value)


async def test_with_no_model_the_rules_still_understand_a_misspelled_request() -> None:
    res = await parse(
        "pay my tutition invoice before due date", TODAY, gateway=NoModel(), packs=PACKS
    )
    assert res.frame.intent is IntentType.PAY_INVOICE
    assert res.cascade.used == "rule" and "rule_fallback" in res.cascade.reasons
    assert res.frame.confidence_bps >= 8000


async def test_with_no_model_and_no_keyword_the_answer_is_still_the_generic_question() -> None:
    res = await parse("banana banana", TODAY, gateway=NoModel(), packs=PACKS)
    assert res.frame.intent is IntentType.OTHER and res.frame.confidence_bps == 0
    assert res.clarify is not None
