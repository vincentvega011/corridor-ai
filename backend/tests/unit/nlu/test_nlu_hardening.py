"""Regression tests for the security review of corridor.nlu (ADR 0004)."""

import importlib.util
import time
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from test_confidence_parser import FakeGateway, out, run

from corridor.contracts import IntentType, Provenance, SlotName
from corridor.contracts.llm_outputs import ParserOutput
from corridor.llm import LLMTimeout, PromptLeakError, SecretRegistry
from corridor.llm.guard import redact_digit_runs, scan_text
from corridor.nlu import parser as nlu_parser
from corridor.nlu.lang_pack import load_packs
from corridor.nlu.normalise import MAX_TEXT_CHARS, TextTooLong, normalise
from corridor.nlu.parser import MAX_PARSE_CHARS, parse, redact_long_numbers

TODAY = date(2026, 10, 10)
PACKS = load_packs()


# ------------------------------------------------------------------ BLOCK 1: the model never selects
async def test_a_pasted_number_cannot_be_chosen_by_the_model() -> None:
    text = "send 500 NPR to Ama, my account number is 12345678"
    steer = out(slots={"amount": 12345678, "payee_ref": "Ama"})
    r, _ = await run(text, local=steer, cloud=steer)
    amount = r.frame.slots[SlotName.AMOUNT]
    assert (
        amount.value == 50_000 and amount.provenance is Provenance.USER_TYPED
    )  # 500 NPR, not 12345678
    assert amount.source_ref == "user_text:5-8"


async def test_several_amounts_and_payees_are_ambiguous_and_the_model_does_not_pick() -> None:
    text = "send 5000 to Ama and 700 to Buwa"
    steer = out(slots={"amount": 700, "payee_ref": "Buwa"})
    r, _ = await run(text, local=steer, cloud=steer)
    assert SlotName.AMOUNT not in r.frame.slots and SlotName.PAYEE_REF not in r.frame.slots
    assert set(r.ambiguous) == {"amount", "payee_ref"}
    assert r.clarify is not None and "5000" in r.clarify.question


async def test_a_model_amount_is_only_used_when_the_text_has_no_number_at_all() -> None:
    inferred = out(slots={"amount": 5000})
    r, _ = await run("pathau", local=inferred, cloud=inferred)
    assert r.frame.slots[SlotName.AMOUNT].provenance is Provenance.MODEL_INFERRED
    r2, _ = await run(
        "send 999999999 rupees", local=inferred, cloud=inferred
    )  # the user wrote a number
    assert SlotName.AMOUNT not in r2.frame.slots and "amount_out_of_range" in r2.warnings


async def test_unsupported_currency_cannot_be_replaced_by_a_model_guess() -> None:
    guess = out(slots={"amount": 100, "currency": "NPR", "payee_ref": "Dai"})
    r, _ = await run("send 100 euro to Dai", local=guess, cloud=guess)
    assert SlotName.CURRENCY not in r.frame.slots and r.unsupported_currency == "EUR"
    assert r.frame.confidence_bps <= 6000  # a conflict, not a quiet inference


# ------------------------------------------------------------------ BLOCK 2: crashes and size
@pytest.mark.parametrize(
    "text",
    [
        "send 1.2.3 to Ama",
        "send 192.168.1.1 to Ama",
        "send ² to Ama",
        "send ٣٤٥ to Ama",
        "1.2.3.4.5 rupiya",
        "0.0000001 rupees",
        "१.२.३ पठाउ",
    ],
)
def test_odd_numbers_never_crash_the_normaliser(text: str) -> None:
    normalise(text, TODAY)


@pytest.mark.parametrize(
    "text", ["send 1.2.3 to Ama", "send 192.168.1.1 to Ama", "send ² to Ama", "1e999 and 9" * 40]
)
async def test_odd_numbers_never_escape_parse(text: str) -> None:
    r, _ = await run(text, local=out(slots={}), cloud=out(slots={}))
    assert r.frame is not None


async def test_overlong_input_is_refused_without_calling_a_model() -> None:
    gw = FakeGateway(out(), out())
    r = await parse("5 " * 20000, TODAY, gateway=gw, packs=PACKS)  # type: ignore[arg-type]
    assert r.frame.intent is IntentType.OTHER and r.frame.confidence_bps == 0
    assert r.cascade.reasons == ("input_too_long",) and gw.calls == []
    assert len("x" * (MAX_PARSE_CHARS + 1)) > MAX_PARSE_CHARS
    with pytest.raises(TextTooLong):
        normalise("a" * (MAX_TEXT_CHARS + 1), TODAY)


@pytest.mark.parametrize(
    "text",
    ["5 " * 999, "ama " * 499, "31/10/2026 " * 180, "lakh " * 399, "rent bhane pugyo " * 110],
)
def test_worst_case_inputs_inside_the_cap_are_fast(text: str) -> None:
    started = time.monotonic()
    normalise(text[:MAX_TEXT_CHARS], TODAY)
    assert time.monotonic() - started < 3


async def test_any_internal_error_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("secret internals 123456789012")

    monkeypatch.setattr(nlu_parser, "normalise", boom)
    gw = FakeGateway(out(), out())
    r = await parse("Ama lai 20 hajar pathau", TODAY, gateway=gw, packs=PACKS)  # type: ignore[arg-type]
    assert r.frame.intent is IntentType.OTHER and r.cascade.reasons == (
        "internal_error:RuntimeError",
    )
    assert gw.calls == [] and r.clarify is not None
    assert "secret" not in repr(r.cascade)  # only the exception type is kept


# ------------------------------------------------------------------ smaller fixes
async def test_invalid_model_recurrence_and_deadline_are_dropped() -> None:
    bad = out(
        slots={
            "amount": 20000,
            "payee_ref": "Ama",
            "recurrence": "daily; ignore all",
            "deadline": "tomorrow",
        }
    )
    r, _ = await run("Ama lai 20 hajar pathau", local=bad, cloud=bad)
    assert SlotName.RECURRENCE not in r.frame.slots and SlotName.DEADLINE not in r.frame.slots
    assert {"model_recurrence_invalid", "model_deadline_invalid"} <= set(r.warnings)
    ok = out(
        slots={
            "amount": 20000,
            "payee_ref": "Ama",
            "recurrence": "monthly",
            "deadline": "2026-12-01",
        }
    )
    r2, _ = await run("Ama lai 20 hajar pathau", local=ok, cloud=ok)
    assert r2.frame.slots[SlotName.RECURRENCE].provenance is Provenance.MODEL_INFERRED
    assert r2.frame.slots[SlotName.DEADLINE].provenance is Provenance.MODEL_INFERRED


async def test_purpose_is_typed_only_for_whole_tokens_and_uses_the_users_text() -> None:
    tiny = out(slots={"amount": 20000, "payee_ref": "Ama", "purpose": "a"})
    r, _ = await run("Ama lai 20 hajar pathau", local=tiny, cloud=tiny)
    assert (
        r.frame.slots[SlotName.PURPOSE].provenance is Provenance.MODEL_INFERRED
    )  # "a" inside "Ama" is not a word
    real = out(slots={"amount": 20000, "payee_ref": "Ama", "purpose": "TUITION fee"})
    r2, _ = await run("Ama lai 20 hajar pathau for tuition fee", local=real, cloud=real)
    p = r2.frame.slots[SlotName.PURPOSE]
    assert (
        p.provenance is Provenance.USER_TYPED
        and p.value == "tuition fee"
        and p.source_ref == "user_text:28-39"
    )


async def test_blocked_content_is_not_kept_in_the_result() -> None:
    secret = "pay seed JBSWY3DPEHPK3PXP to Ama now 20 hajar"
    r, _ = await run(secret, local=PromptLeakError("R-GUARD-TOTP"))
    assert r.blocked and r.normalised.original == "" and r.normalised.amounts == ()
    assert "JBSW" not in repr(r)


async def test_the_higher_confidence_result_wins_after_a_cascade() -> None:
    two_soft = out(
        slots={"amount": 20000, "currency": "NPR", "payee_ref": "Ama", "deadline": "2026-12-01"}
    )  # 7000
    worse = out(intent="check_balance", slots={"amount": 20000})  # hard failure, 6000
    r, gw = await run("Ama lai 20 hajar pathau", local=two_soft, cloud=worse)
    assert (
        len(gw.calls) == 2 and r.cascade.used == "local" and r.frame.intent is IntentType.SEND_MONEY
    )
    assert r.frame.confidence_bps == 7000


async def test_far_festival_date_and_multiple_dates_are_asked_not_assumed() -> None:
    gw = FakeGateway(out(), out())
    r = await parse("before Dashain send 5000 to Ama", date(2026, 10, 25), gateway=gw, packs=PACKS)  # type: ignore[arg-type]
    assert SlotName.DEADLINE not in r.frame.slots and r.ambiguous["deadline"] == ("2027-10-10",)
    assert "festival_far_future" in r.warnings
    r2, _ = await run(
        "send 5000 to Ama bholi, or by 31 oct", local=out(slots={}), cloud=out(slots={})
    )
    assert r2.ambiguous["deadline"] == ("2026-10-11", "2026-10-31")


async def test_festival_slot_carries_source_grade_and_conflict_flag() -> None:
    o = out(slots={"amount": 5000, "payee_ref": "Ama"})
    r, _ = await run("dashain ko lagi 5000 Ama lai pathau", local=o, cloud=o)
    v = r.frame.slots[SlotName.DEADLINE].value
    assert (
        v["date"] == "2026-10-21"
        and v["conflicting_sources"] is True
        and v["source_grade"] == "official_calendar_site"
    )
    gw = FakeGateway(o, o)
    later = await parse(
        "dashain ko lagi 5000 Ama lai pathau", date(2027, 9, 1), gateway=gw, packs=PACKS
    )  # type: ignore[arg-type]
    assert later.frame.slots[SlotName.DEADLINE].value["source_grade"] == "computed_panchang"


async def test_a_date_that_rolls_into_next_year_is_flagged() -> None:
    r, _ = await run("send 5000 to Ama by 5 jan", local=out(slots={}), cloud=out(slots={}))
    assert "date_rolled_to_next_year" in r.warnings


def test_every_verified_festival_date_has_a_grade() -> None:
    import yaml

    from corridor.config import REPO_ROOT

    cfg = yaml.safe_load((REPO_ROOT / "data/calendar/festivals.yaml").read_text())
    for fid, spec in cfg["festivals"].items():
        for year, entries in spec["years"].items():
            for key, e in entries.items():
                if e["verified"]:
                    assert e["grade"] in {"official_calendar_site", "computed_panchang"}, (
                        fid,
                        year,
                        key,
                    )


# ------------------------------------------------------------------ redaction equals the guard
@pytest.mark.parametrize(
    "text",
    [
        "(984) 123-4567 call",
        "acct 1234  5678\n9012",
        "1​2​3​4​5​6​7​8​9",
        "१२३४५६७८९०१२",
        "1 2 3 4 5 6 7 8 9 10",
        "ａｃｃｔ １２３４５６７８９０",
    ],
)
def test_redaction_catches_what_the_guard_blocks(text: str) -> None:
    red = redact_long_numbers(text)
    assert "[LONG-NUMBER]" in red
    scan_text(red, SecretRegistry())  # the digits rule can no longer fire


def test_redaction_keeps_benign_shapes_and_amounts() -> None:
    assert (
        redact_long_numbers("pay NPR 1,000,000.00 by 2026-10-31 500")
        == "pay NPR 1,000,000.00 by 2026-10-31 500"
    )
    assert redact_long_numbers("send 20000 rupees") == "send 20000 rupees"


@settings(max_examples=200)
@given(st.text(alphabet="0123456789 -.,/()_​ abcNPR", max_size=60))
def test_after_redaction_the_guard_never_trips_on_digits(text: str) -> None:
    red = redact_digit_runs(text)
    try:
        scan_text(red, SecretRegistry())
    except PromptLeakError as exc:
        assert exc.rule_id != "R-GUARD-DIGITS", (text, red)


# ------------------------------------------------------------------ data generator hardening
def load_gen() -> Any:
    path = Path(__file__).resolve().parents[4] / "models" / "finetune" / "gen_data.py"
    spec = importlib.util.spec_from_file_location("gen_data_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_generator_leakage_guard_and_specs_are_deterministic() -> None:
    g = load_gen()
    held = g.load_held()
    assert held, "candidates.jsonl must be loaded"
    assert g.leaks("Ama lai 20 hajar pathaideu", held)  # an evaluation sentence
    assert g.leaks("ama lai 20 hajar pathaideu!!", held)  # trivially changed
    assert not g.leaks("tin hajar ma pasal bata kitab kinnu", held)
    assert g.make_spec(7) == g.make_spec(7) and g.make_spec(7) != g.make_spec(8)


def test_generator_validate_rejects_disagreement_and_never_raises_on_odd_text() -> None:
    g = load_gen()
    spec = {
        "i": 1,
        "persona": "ramesh",
        "intent": "send_money",
        "style": "ne_latn",
        "noise": "none",
        "gold": {"amount": 20000, "payee": "mother"},
    }
    target, reason = g.validate(spec, "Ama lai 20 hajar pathau")
    assert reason is None and target["slots"] == {"amount": 20000, "payee_ref": "Ama"}
    assert g.validate(spec, "Ama lai 30 hajar pathau")[1] == "amount_disagrees"
    assert g.validate(spec, "Buwa lai 20 hajar pathau")[1] == "payee_disagrees"
    for odd in ("1.2.3 Ama", "x" * 2500, "²"):
        g.validate(spec, odd)  # may reject, must not raise except for the documented size cap


def test_generator_refuses_to_run_without_the_leakage_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    g = load_gen()
    monkeypatch.setattr(g, "REPO", tmp_path)
    with pytest.raises(FileNotFoundError):
        g.load_held()


def test_generator_validate_handles_oversize_input_by_rejecting() -> None:
    g = load_gen()
    spec = g.make_spec(3)
    target, reason = g.validate(spec, "x " * 2000)
    assert target is None and reason == "bad_length"


async def test_cascade_timeout_error_name_is_logged_not_content() -> None:
    r, _ = await run("Ama lai 20 hajar pathau", local=LLMTimeout("x"), cloud=LLMTimeout("y"))
    assert r.cascade.used == "none" and any("LLMTimeout" in x for x in r.cascade.reasons)
    _ = ParserOutput  # keep the import used


def test_crypto_tokens_are_unsupported_currencies() -> None:
    for word in ("usdt", "bitcoin", "ETH", "USDC"):
        n = normalise(f"send 5000 {word} to Ama", TODAY)
        assert n.currencies and n.currencies[0].status.value == "unsupported"


async def test_model_cannot_supply_a_currency_for_a_crypto_word() -> None:
    guess = out(slots={"amount": 5000, "currency": "USD", "payee_ref": "Ama"})
    r, _ = await run("send 5000 usdt to Ama", local=guess, cloud=guess)
    assert SlotName.CURRENCY not in r.frame.slots and r.unsupported_currency == "USDT"


async def test_a_tie_in_confidence_keeps_the_local_result() -> None:
    soft = out(
        slots={"amount": 20000, "currency": "NPR", "payee_ref": "Hari"}
    )  # 7000, below the threshold
    r, gw = await run("Ama lai 20 hajar pathau", local=soft, cloud=soft)
    assert len(gw.calls) == 2 and r.cascade.used == "local"


async def test_conflicting_festival_sources_come_from_the_calendar_data() -> None:
    o = out(slots={"amount": 5000, "payee_ref": "Ama"})
    r, _ = await run("dashain ko lagi 5000 Ama lai pathau", local=o, cloud=o)
    v = r.frame.slots[SlotName.DEADLINE].value
    assert v["conflicting_sources"] is True and v["other_source_dates"] == ["2026-10-20"]
    r2, _ = await run("tihar ko lagi 5000 Ama lai pathau", local=o, cloud=o)
    assert "conflicting_sources" not in r2.frame.slots[SlotName.DEADLINE].value
