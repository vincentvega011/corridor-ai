"""Verifier based confidence, evidence backed provenance, the cascade and clarification."""

from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest
import yaml
from pydantic import BaseModel

from corridor.config import REPO_ROOT
from corridor.contracts import IntentType, LanguageTag, Provenance, SlotName
from corridor.contracts.llm_outputs import ParserOutput
from corridor.llm import LLMSchemaError, LLMTimeout, ModelRole, PromptLeakError
from corridor.nlu import confidence as conf
from corridor.nlu.clarify import STYLES, build_clarify, question_for
from corridor.nlu.lang_pack import load_packs
from corridor.nlu.normalise import normalise
from corridor.nlu.parser import PLACEHOLDER, build_messages, parse, redact_long_numbers
from corridor.nlu.rules import classify_intent

TODAY = date(2026, 10, 10)
PACKS = load_packs()


# --------------------------------------------------------------------------- formula
@pytest.mark.parametrize(
    ("valid", "hard", "soft", "abstain", "expected"),
    [
        (True, 0, 0, False, 10000),
        (True, 0, 0, True, 9500),
        (True, 0, 1, False, 8500),
        (True, 0, 1, True, 8000),  # exactly the threshold: no cascade
        (True, 0, 2, False, 7000),
        (True, 0, 2, True, 6500),
        (True, 1, 0, False, 6000),
        (True, 1, 1, False, 6000),  # the cap binds first
        (True, 2, 0, True, 6000),
        (True, 0, 7, True, 0),  # never negative
        (False, 0, 0, False, 0),
    ],
)
def test_confidence_formula_boundaries(
    valid: bool, hard: int, soft: int, abstain: bool, expected: int
) -> None:
    assert conf.score(valid, hard, soft, abstain) == expected


def test_cascade_threshold_boundary() -> None:
    assert conf.CASCADE_THRESHOLD_BPS == 8000
    ok = conf.Verification(True, 8000)
    low = conf.Verification(True, 7999)
    bad = conf.Verification(False, 0)
    assert not ok.needs_cascade and low.needs_cascade and bad.needs_cascade


def out(**kw: Any) -> ParserOutput:
    base: dict[str, Any] = {
        "language": "en",
        "intent": "send_money",
        "slots": {},
        "confidence_bps": 10000,
    }
    base.update(kw)
    return ParserOutput.model_validate(base)


def ver(text: str, **kw: Any) -> conf.Verification:
    norm = normalise(text, TODAY)
    return conf.verify(out(**kw), norm, classify_intent(text, norm), PACKS)


def test_a_clean_output_scores_full_marks() -> None:
    v = ver("Ama lai 20 hajar pathau", slots={"amount": 20000, "payee_ref": "Ama"})
    assert v.confidence_bps == 10000 and not v.hard and not v.soft


@pytest.mark.parametrize(
    ("text", "kw", "hard"),
    [
        ("Ama lai 20 hajar pathau", {"slots": {"amount": 2000}}, "amount_mismatch"),
        ("Ama lai pathau", {"slots": {"amount": 500}}, "amount_mismatch"),  # hallucinated amount
        ("Ama lai 20 hajar pathau", {"intent": "check_balance"}, "intent_disagrees"),
        (
            "500 riyal Ama lai pathau",
            {"slots": {"amount": 500, "currency": "AUD"}},
            "currency_conflict",
        ),
        (
            "500 dollar Ama lai pathau",
            {"slots": {"amount": 500, "currency": "USD"}},
            "currency_conflict",
        ),
    ],
)
def test_hard_failures_always_cascade(text: str, kw: dict[str, Any], hard: str) -> None:
    v = ver(text, **kw)
    assert hard in v.hard and v.confidence_bps <= 6000 and v.needs_cascade


@pytest.mark.parametrize(
    ("text", "kw", "soft"),
    [
        (
            "Ama lai 20 hajar pathau",
            {"slots": {"amount": 20000, "currency": "NPR"}},
            "currency_without_evidence",
        ),
        (
            "Ama lai 20 hajar pathau",
            {"slots": {"amount": 20000, "deadline": "2026-12-01"}},
            "deadline_mismatch",
        ),
        (
            "Ama lai 20 hajar pathau",
            {"slots": {"amount": 20000, "payee_ref": "Hari"}},
            "payee_ungrounded",
        ),
        (
            "Ama lai 20 hajar pathau",
            {"slots": {"amount": 20000, "recurrence": "monthly"}},
            "recurrence_without_evidence",
        ),
    ],
)
def test_soft_failures_cost_1500_each(text: str, kw: dict[str, Any], soft: str) -> None:
    v = ver(text, **kw)
    assert soft in v.soft and v.confidence_bps == 8500


def test_two_soft_failures_cascade() -> None:
    v = ver(
        "Ama lai 20 hajar pathau", slots={"amount": 20000, "currency": "NPR", "payee_ref": "Hari"}
    )
    assert v.confidence_bps == 7000 and v.needs_cascade


def test_classifier_abstention_is_neutral() -> None:
    v = ver("hello there", intent="other")
    assert v.abstained and v.confidence_bps == 9500


def test_invalid_output_is_confidence_zero() -> None:
    norm = normalise("x", TODAY)
    v = conf.verify(None, norm, classify_intent("x", norm), PACKS)
    assert v.confidence_bps == 0 and v.needs_cascade


def test_keyword_classifier_examples() -> None:
    cases = {
        "Ama lai 20 hajar pathau": IntentType.SEND_MONEY,
        "mero balance kati chha": IntentType.CHECK_BALANCE,
        "har mahina Ama lai 5 hajar pathau": IntentType.SCHEDULE_TRANSFER,
        "compare the cheapest way to send": IntentType.COMPARE_ROUTES,
        "show my spending": IntentType.SPENDING_SUMMARY,
        "why was it blocked": IntentType.EXPLAIN_DECISION,
    }
    for text, intent in cases.items():
        assert classify_intent(text, today=TODAY).intent is intent, text
    assert classify_intent("lorem ipsum", today=TODAY).intent is None


# --------------------------------------------------------------------------- fake gateway
@dataclass
class FakeResult:
    parsed: BaseModel | None


class FakeGateway:
    """Returns scripted outputs per role; an Exception instance is raised instead."""

    def __init__(self, local: Any = None, cloud: Any = None) -> None:
        self.script = {ModelRole.PARSER: local, ModelRole.PARSER_FALLBACK: cloud}
        self.calls: list[tuple[ModelRole, list[dict[str, Any]]]] = []

    async def complete(
        self, role: ModelRole, messages: list[dict[str, Any]], schema: Any = None, tools: Any = None
    ) -> FakeResult:
        self.calls.append((role, messages))
        item = self.script[role]
        if isinstance(item, Exception):
            raise item
        return FakeResult(item)

    def roles(self) -> list[ModelRole]:
        return [r for r, _ in self.calls]


GOOD = out(slots={"amount": 20000, "payee_ref": "Ama"})


async def run(text: str, local: Any = GOOD, cloud: Any = None) -> tuple[Any, FakeGateway]:
    gw = FakeGateway(local, cloud)
    return await parse(text, TODAY, gateway=gw, packs=PACKS), gw  # type: ignore[arg-type]


async def test_confident_local_result_does_not_cascade() -> None:
    r, gw = await run("Ama lai 20 hajar pathau")
    assert gw.roles() == [ModelRole.PARSER] and r.cascade.used == "local" and not r.cascade.cascaded
    assert r.frame.parser == "local" and r.frame.confidence_bps == 10000


async def test_low_confidence_cascades_to_cloud_with_logged_reason() -> None:
    bad = out(intent="check_balance", slots={"amount": 7})
    r, gw = await run("Ama lai 20 hajar pathau", local=bad, cloud=GOOD)
    assert gw.roles() == [ModelRole.PARSER, ModelRole.PARSER_FALLBACK]
    assert r.cascade.cascaded and r.cascade.used == "cloud" and r.frame.parser == "cloud"
    assert any("confidence_below_8000" in x for x in r.cascade.reasons)
    assert r.cascade.local_confidence_bps is not None and r.cascade.local_confidence_bps <= 6000
    assert r.frame.intent is IntentType.SEND_MONEY and r.frame.confidence_bps == 10000


@pytest.mark.parametrize("err", [LLMSchemaError("bad"), LLMTimeout("slow")])
async def test_invalid_or_failed_local_cascades(err: Exception) -> None:
    r, gw = await run("Ama lai 20 hajar pathau", local=err, cloud=GOOD)
    assert gw.roles() == [ModelRole.PARSER, ModelRole.PARSER_FALLBACK] and r.cascade.used == "cloud"
    assert type(err).__name__ in " ".join(r.cascade.reasons)


async def test_both_models_failing_fails_closed() -> None:
    r, _ = await run("Ama lai 20 hajar pathau", local=LLMTimeout("x"), cloud=LLMSchemaError("y"))
    assert (
        r.frame.intent is IntentType.OTHER
        and r.frame.confidence_bps == 0
        and r.cascade.used == "none"
    )
    assert r.clarify is not None and r.frame.slots == {}


async def test_cloud_failure_with_unusable_local_output_does_not_act() -> None:
    low = out(intent="check_balance")
    r, _ = await run("Ama lai 20 hajar pathau", local=low, cloud=LLMTimeout("x"))
    assert (
        r.frame.confidence_bps < 8000 and r.clarify is not None
    )  # asks the user instead of acting


async def test_prompt_guard_block_is_reported_not_sent_on() -> None:
    r, gw = await run("pay seed JBSWY3DPEHPK3PXP now", local=PromptLeakError("R-GUARD-TOTP"))
    assert r.blocked and r.frame.intent is IntentType.OTHER and gw.roles() == [ModelRole.PARSER]
    assert r.clarify is not None and r.clarify.style in ("ne_latn", "en")


# --------------------------------------------------------------------------- provenance
async def test_evidence_backed_slots_are_user_typed_with_a_source_span() -> None:
    text = "Ama lai 20 hajar rupiya pathau"
    r, _ = await run(text)
    s = r.frame.slots
    assert (
        s[SlotName.AMOUNT].provenance is Provenance.USER_TYPED
        and s[SlotName.AMOUNT].value == 2_000_000
    )
    assert s[SlotName.AMOUNT].source_ref == "user_text:8-16"
    assert (
        s[SlotName.CURRENCY].provenance is Provenance.USER_TYPED
        and s[SlotName.CURRENCY].value == "NPR"
    )
    assert s[SlotName.PAYEE_REF].value == {"text": "Ama", "relation": "mother"}
    assert s[SlotName.PAYEE_REF].source_ref.startswith("user_text:")


async def test_values_without_evidence_are_model_inferred_never_user_typed() -> None:
    inferred = out(slots={"amount": 20000, "currency": "NPR", "payee_ref": "Hari"})
    r, _ = await run("pathau", local=inferred, cloud=inferred)
    s = r.frame.slots
    assert (
        s[SlotName.AMOUNT].provenance is Provenance.MODEL_INFERRED
        and s[SlotName.AMOUNT].source_ref == "model_inferred"
    )
    assert s[SlotName.CURRENCY].provenance is Provenance.MODEL_INFERRED
    assert s[SlotName.PAYEE_REF].provenance is Provenance.MODEL_INFERRED
    assert all(v.provenance is not Provenance.USER_TYPED for v in s.values())


async def test_evidence_beats_the_model_when_they_disagree() -> None:
    wrong = out(slots={"amount": 99, "payee_ref": "Ama"})
    r, _ = await run("Ama lai 20 hajar pathau", local=wrong, cloud=wrong)
    assert r.frame.slots[SlotName.AMOUNT].value == 2_000_000
    assert r.frame.slots[SlotName.AMOUNT].provenance is Provenance.USER_TYPED


async def test_model_cannot_set_provenance_or_extra_fields() -> None:
    with pytest.raises(Exception, match="provenance"):
        ParserOutput.model_validate(
            {
                "language": "en",
                "intent": "help",
                "slots": {},
                "confidence_bps": 1,
                "provenance": "system",
            }
        )
    from corridor.contracts import IntentFrame, Tainted

    with pytest.raises(Exception, match="slot"):
        IntentFrame(
            language=LanguageTag.EN,
            intent=IntentType.HELP,
            slots={SlotName.AMOUNT: Tainted[Any](value=1, provenance=Provenance.SYSTEM)},
            confidence_bps=1,
            parser="local",
        )


async def test_ambiguous_currency_is_never_guessed_and_asks() -> None:
    chose_usd = out(slots={"amount": 500, "payee_ref": "Dai", "currency": "USD"})
    r, _ = await run("send 500 dollar to Dai", local=chose_usd, cloud=chose_usd)
    assert SlotName.CURRENCY not in r.frame.slots
    assert r.ambiguous == {"currency": ("AUD", "USD")}
    assert r.clarify is not None and "AUD / USD" in r.clarify.question


async def test_festival_deadline_slot_has_the_normaliser_date() -> None:
    o = out(slots={"amount": 30000, "payee_ref": "Suresh"})
    r, _ = await run("dashain bhanda agadi Suresh lai 30k send gardeu", local=o)
    d = r.frame.slots[SlotName.DEADLINE]
    assert d.provenance is Provenance.USER_TYPED
    assert d.value == {
        "kind": "before_festival",
        "festival": "dashain",
        "date": "2026-10-21",
        "verified": True,
        "source_grade": "official_calendar_site",
        "conflicting_sources": True,
        "other_source_dates": ["2026-10-20"],
    }


async def test_condition_and_recurrence_slots() -> None:
    o = out(
        intent="schedule_transfer",
        slots={"amount": 10000, "payee_ref": "Ama", "recurrence": "monthly"},
    )
    r, _ = await run("har mahina rent pugyo bhane matra Ama lai 10 hajar pathau", local=o)
    assert r.frame.slots[SlotName.CONDITION].value["reserve"] == "rent"
    assert r.frame.slots[SlotName.RECURRENCE].value == {"freq": "monthly"}
    assert all(s.provenance is Provenance.USER_TYPED for s in r.frame.slots.values())


# --------------------------------------------------------------------------- prompt safety
def test_long_numbers_are_redacted_before_any_model_sees_the_text() -> None:
    assert redact_long_numbers("pay 912738032125 now") == f"pay {PLACEHOLDER} now"
    assert PLACEHOLDER in redact_long_numbers("acct १२३४५६७८९०१२")
    assert redact_long_numbers("send 20000 to 1234 5678 9012 ok").count(PLACEHOLDER) == 1
    assert redact_long_numbers("send 12345678") == "send 12345678"
    text = "Ama lai 20 hajar pathau, khata 912738032125"
    msgs = build_messages(text, normalise(text, TODAY), PACKS)
    assert "912738032125" not in " ".join(m["content"] for m in msgs)


async def test_only_user_text_reaches_the_model_prompt() -> None:
    r, gw = await run("Ama lai 20 hajar pathau")
    last = gw.calls[0][1][-1]["content"]
    assert last.startswith("Message: Ama lai 20 hajar pathau\nHints: ")
    assert gw.calls[0][1][0]["role"] == "system"


def test_prompt_messages_pass_the_gateway_guard_for_large_amounts() -> None:
    from corridor.llm.guard import SecretRegistry, scan_messages

    for text in ("send 5 crore to Ama", "10 lakh 50 hajar pathau", "send 99,999,999 rupees to Dai"):
        msgs = build_messages(text, normalise(text, TODAY), PACKS)
        scan_messages(msgs, SecretRegistry())


# --------------------------------------------------------------------------- clarification
def test_clarify_templates_exist_for_every_required_slot_in_every_style() -> None:
    required = yaml.safe_load((REPO_ROOT / "data/nlu/slots.yaml").read_text())["required"]
    slots = {s for slots in required.values() for s in slots}
    assert {"amount", "payee_ref", "recurrence"} <= slots
    for slot in slots:
        for style in STYLES:
            assert question_for(slot, style, PACKS).strip(), (slot, style)
    for style in STYLES:
        assert "{options}" not in question_for("currency", style, PACKS, ("AUD", "USD"))
        assert "AUD / USD" in question_for("currency", style, PACKS, ("AUD", "USD"))
        assert PACKS.clarify["generic"][style] and PACKS.clarify["blocked_content"][style]
        assert PACKS.clarify["deadline"][style]
        for slot in ("amount", "payee_ref", "currency"):
            assert "Ama / Dai" in question_for(slot, style, PACKS, ("Ama", "Dai"))


@pytest.mark.parametrize(
    ("language", "deva", "marker"),
    [
        (LanguageTag.EN, False, "How much"),
        (LanguageTag.NE, True, "कति रकम"),
        (LanguageTag.NE_LATN, False, "Kati rakam"),
        (LanguageTag.MIXED, False, "Kati rakam"),
        (LanguageTag.MIXED, True, "कति रकम"),
    ],
)
def test_clarify_style_follows_the_language(language: LanguageTag, deva: bool, marker: str) -> None:
    c = build_clarify(("amount",), {}, language, deva, PACKS)
    assert c is not None and marker in c.question and c.missing_slots == ("amount",)


def test_clarify_none_when_nothing_missing_and_two_questions_at_most() -> None:
    assert build_clarify((), {}, LanguageTag.EN, False, PACKS) is None
    c = build_clarify(("amount", "payee_ref", "recurrence"), {}, LanguageTag.EN, False, PACKS)
    assert c is not None and c.question.count("?") == 2


async def test_missing_slots_trigger_a_templated_question() -> None:
    o = out(slots={})
    r, _ = await run("paisa pathau", local=o)
    assert [s.value for s in r.frame.missing_slots] == ["amount", "payee_ref"]
    assert r.clarify is not None and "Kati rakam" in r.clarify.question
    assert r.clarify.missing_slots == ("amount", "payee_ref")
