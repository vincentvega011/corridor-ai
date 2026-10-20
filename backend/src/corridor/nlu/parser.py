"""The parser cascade: deterministic normaliser, local model, verifier, cloud fallback.

Flow (docs/spec/modules.md 5.3, ADR 0004):
1. The normaliser extracts candidates with evidence spans from the user's own text.
2. The local parser model (role ``parser``) returns a ``ParserOutput``. It gets the message and the
   normaliser's candidates as hints. The message is only the user's own words (INV-3): documents,
   images and messages from third parties never reach this module.
3. The output is verified against the evidence and the keyword classifier; ``confidence_bps`` comes
   from ``confidence.py``, never from the model.
4. If the result is invalid or ``confidence_bps < 8000`` the cloud ``parser_fallback`` is called.
5. Slots and provenance are built by code from the evidence (``slots.py``). The model never sets
   provenance. Missing or ambiguous slots become a templated clarification, no LLM.

Fail closed (INV-10): if both models fail, the frame is intent ``other`` with confidence 0 and a
"please rephrase" clarification; nothing is guessed. Digit runs of 9 or more are replaced by a
placeholder before any model sees the text (INV-12), and content that trips the prompt guard is
reported as ``blocked`` instead of being sent anywhere.
"""

import json
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import structlog

from corridor.contracts.intent import IntentFrame, IntentType, LanguageTag
from corridor.contracts.llm_outputs import ParserOutput
from corridor.llm import Gateway, LLMError, ModelRole, PromptLeakError, get_gateway
from corridor.llm.guard import mask_account_numbers, redact_digit_runs
from corridor.llm.guard import normalise as normalise_text
from corridor.nlu.clarify import ClarifyRequest, build_clarify, generic_question
from corridor.nlu.confidence import CASCADE_THRESHOLD_BPS, Verification, verify
from corridor.nlu.lang_pack import LangPacks, load_packs
from corridor.nlu.normalise import Normalised, Script, normalise
from corridor.nlu.rules import IntentGuess, classify_intent
from corridor.nlu.slots import SlotBuild, build_slots, make_frame
from corridor.nlu.typed_payee import digit_runs

log = structlog.get_logger("corridor.nlu")
PROMPT_DIR = Path(__file__).parent / "prompts"
EXAMPLE_TODAY = date(2026, 10, 10)
PLACEHOLDER = "[LONG-NUMBER]"
MAX_PARSE_CHARS = 1000  # a chat message; longer input is refused (fail closed)


def redact_long_numbers(text: str) -> str:
    """Replace digit runs the prompt guard would block (9 or more digits, any separators,
    Devanagari digits, zero-width characters) with a placeholder, so no full number reaches a prompt."""
    return redact_digit_runs(text, PLACEHOLDER)


def hints_for(norm: Normalised) -> dict[str, Any]:
    """Deterministic extractions given to the model as hints (all guard friendly)."""
    h: dict[str, Any] = {"language": norm.language.value}
    amounts = []
    for a in norm.amounts:
        bare = not (a.currency and a.currency.code)
        if bare and mask_account_numbers(a.span.text) != normalise_text(a.span.text):
            continue  # a bare run of 6 or more digits is an account-like number, not an amount (INV-12)
        item: dict[str, Any] = {"text": a.span.text}
        if a.in_range:
            item["units"] = int(a.units) if "." not in a.units else a.units
        if a.currency and a.currency.code:
            item["currency"] = a.currency.code.value
        amounts.append(item)
    if amounts:
        h["amounts"] = amounts
    cur = [
        {
            "text": c.span.text,
            "status": c.status.value,
            **({"options": list(c.options)} if c.options else {}),
        }
        for c in norm.currencies
    ]
    if cur:
        h["currency_words"] = cur
    if norm.relations:
        h["relations"] = [{"text": r.term, "relation": r.relation} for r in norm.relations]
    if norm.names:
        h["names"] = [n.name for n in norm.names]
    dates = [{"text": d.span.text, "date": d.date.isoformat()} for d in norm.dates if d.date]
    dates += [{"text": f.span.text, "date": f.date.isoformat()} for f in norm.festivals if f.date]
    if dates:
        h["dates"] = dates
    if norm.recurrences:
        h["recurrence"] = norm.recurrences[0].freq
    if norm.conditions:
        h["condition"] = f"only if {norm.conditions[0].reserve} is covered"
    return h


def user_message(text: str, norm: Normalised) -> str:
    """The final user turn: the redacted message plus redacted hints."""
    hints = redact_long_numbers(json.dumps(hints_for(norm), ensure_ascii=False))
    return f"Message: {mask_account_numbers(redact_long_numbers(text))}\nHints: {hints}"


def build_messages(text: str, norm: Normalised, packs: LangPacks) -> list[dict[str, Any]]:
    """System prompt, few-shot turns (hints computed by the same code), then the real message."""
    system = (PROMPT_DIR / "parser_v1.txt").read_text(encoding="utf-8").strip()
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for ex in json.loads((PROMPT_DIR / "parser_v1_examples.json").read_text(encoding="utf-8")):
        ex_norm = normalise(ex["text"], EXAMPLE_TODAY, packs)
        messages.append({"role": "user", "content": user_message(ex["text"], ex_norm)})
        messages.append(
            {"role": "assistant", "content": json.dumps(ex["output"], ensure_ascii=False)}
        )
    messages.append({"role": "user", "content": user_message(text, norm)})
    return messages


RULE_FALLBACK_BPS = 8500
"""Confidence of a rule-only parse when no model is available: above the asking threshold (8000), below a model's 9000+."""
MODEL_FAILURES = (
    "CassetteMiss",
    "LLMTimeout",
    "LLMUnavailable",
    "QuotaExhausted",
    "LLMConfigError",
)


RULE_FALLBACK_FAILURES = ("CassetteMiss", "LLMUnavailable", "QuotaExhausted", "LLMConfigError")
"""Failures after which the keyword rules may stand in for the model. A timeout is not one of them: a slow model that
may still answer keeps the fail closed behaviour (test_cascade_timeout_error_name_is_logged_not_content)."""


def _models_unavailable(reasons: list[str]) -> bool:
    """True when the cascade ended because no model could answer, with no invalid answer among the errors."""
    invalid = any("SchemaError" in r or "invalid_output" in r or "Timeout" in r for r in reasons)
    return not invalid and any(name in r for r in reasons for name in RULE_FALLBACK_FAILURES)


@dataclass(frozen=True)
class CascadeDecision:
    """Which parser produced the result and why the cascade did or did not trigger."""

    used: str  # local | cloud | none
    cascaded: bool
    reasons: tuple[str, ...]
    local_confidence_bps: int | None
    final_confidence_bps: int


@dataclass(frozen=True)
class ParseResult:
    """Everything the agent needs from one parse."""

    frame: IntentFrame
    normalised: Normalised
    verification: Verification | None
    cascade: CascadeDecision
    clarify: ClarifyRequest | None
    ambiguous: dict[str, tuple[str, ...]] = field(default_factory=dict)
    unsupported_currency: str | None = None
    warnings: tuple[str, ...] = ()
    blocked: bool = False
    latency_ms: int = 0
    llm_calls: int = 0
    raw_output: str = (
        ""  # the chosen model's JSON, account-like digits masked, for the officer trace
    )


def _masked_raw(out: ParserOutput) -> str:
    """The parser model's own JSON as plain text for the officer trace (INV-12: digit runs masked)."""
    return mask_account_numbers(out.model_dump_json())[:1500]


async def _try(
    gw: Gateway,
    role: ModelRole,
    messages: list[dict[str, Any]],
    norm: Normalised,
    guess: IntentGuess,
    packs: LangPacks,
) -> tuple[ParserOutput | None, Verification, str | None, bool]:
    """One model attempt: (output, verification, error name, blocked)."""
    try:
        res = await gw.complete(role, messages, schema=ParserOutput)
    except PromptLeakError:
        return None, Verification(False, 0, reasons=("prompt_blocked",)), "PromptLeakError", True
    except LLMError as exc:
        return (
            None,
            Verification(False, 0, reasons=(type(exc).__name__,)),
            type(exc).__name__,
            False,
        )
    out = res.parsed if isinstance(res.parsed, ParserOutput) else None
    return out, verify(out, norm, guess, packs), None, False


def _failed_closed(
    norm: Normalised, deva: bool, packs: LangPacks, reasons: tuple[str, ...], started: float, calls: int,
    blocked: bool = False, verification: Verification | None = None, local_conf: int | None = None,
) -> ParseResult:  # fmt: skip
    """The fail closed result: intent ``other``, confidence 0, a templated "please rephrase"."""
    frame = IntentFrame(
        language=norm.language, intent=IntentType.OTHER, confidence_bps=0, parser="local"
    )
    return ParseResult(
        frame, norm, verification, CascadeDecision("none", False, reasons, local_conf, 0),
        generic_question(norm.language, deva, packs, blocked=blocked), blocked=blocked,
        latency_ms=int((time.monotonic() - started) * 1000), llm_calls=calls,
    )  # fmt: skip


async def parse(
    text: str,
    today: date,
    gateway: Gateway | None = None,
    packs: LangPacks | None = None,
) -> ParseResult:
    """Parse one user message into an ``IntentFrame`` (see module docstring).

    ``text`` must be the user's own typed turn and nothing else (INV-3): never attachment text,
    never a third party message. Any unexpected exception returns the fail closed result."""
    started = time.monotonic()
    packs = packs or load_packs()
    blank = Normalised(original="", script=Script.NONE, language=LanguageTag.EN, today=today)
    deva = any("\u0900" <= c <= "\u097f" for c in text)
    if len(text) > MAX_PARSE_CHARS:
        return _failed_closed(blank, deva, packs, ("input_too_long",), started, 0)
    try:
        return await _parse(text, today, gateway, packs, started, deva)
    except Exception as exc:
        log.error("nlu_internal_error", error=type(exc).__name__)
        return _failed_closed(
            blank, deva, packs, (f"internal_error:{type(exc).__name__}",), started, 0
        )


def _add_payee_by_rule(
    text: str, norm: Normalised, packs: LangPacks, started: float, deva: bool
) -> ParseResult:
    """ "add payee ... account 0620..." is decided in code, never by a model (ADR 0018).

    The words of the request match the add payee phrases and carry an account number, so the intent is
    ADD_PAYEE. This only selects an intent: the number and the name are read from the user's own words by
    ``corridor.nlu.typed_payee``, saving needs MFA and a confirmation of the exact card, and the origin
    of the payee is decided by code. No model is called, so a failing or blocked model cannot turn the
    request into something else (and the fine tuned parser, which saw few such phrases, is not needed)."""
    out = ParserOutput(
        language=norm.language,
        intent=IntentType.ADD_PAYEE,
        slots={},
        confidence_bps=9500,
    )
    ver = Verification(True, 9500, reasons=("add_payee_by_rule",))
    built: SlotBuild = build_slots(out, norm, packs)
    frame = make_frame(IntentType.ADD_PAYEE, norm, built, 9500, "local", packs)
    clarify = build_clarify(
        tuple(s.value for s in frame.missing_slots), built.ambiguous, norm.language, deva, packs
    )
    return ParseResult(
        frame, norm, ver, CascadeDecision("rule", False, ("add_payee_by_rule",), None, 9500),
        clarify, built.ambiguous, built.unsupported_currency, tuple(built.warnings),
        latency_ms=int((time.monotonic() - started) * 1000), llm_calls=0,
    )  # fmt: skip


async def _parse(
    text: str, today: date, gateway: Gateway | None, packs: LangPacks, started: float, deva: bool
) -> ParseResult:
    gw = gateway or get_gateway()
    norm = normalise(text, today, packs)
    guess = classify_intent(text, norm, packs)
    if guess.intent is IntentType.ADD_PAYEE and digit_runs(text):
        return _add_payee_by_rule(text, norm, packs, started, deva)
    messages = build_messages(text, norm, packs)

    reasons: list[str] = []
    calls = 1
    out, ver, err, blocked = await _try(gw, ModelRole.PARSER, messages, norm, guess, packs)
    local_conf = ver.confidence_bps
    chosen_out, chosen_ver, used = out, ver, "local"
    if blocked:
        # never keep the blocked text: the raw message may contain a secret (and this result may be
        # checkpointed), so the stored normalisation has an empty original
        scrubbed = norm.model_copy(
            update={"original": "", "amounts": (), "names": (), "relations": ()}
        )
        return _failed_closed(
            scrubbed, deva, packs, ("prompt_blocked",), started, calls, True, ver, 0
        )
    cascaded = False
    if ver.needs_cascade:
        cascaded = True
        reasons.append(
            err or "invalid_output"
            if not ver.schema_valid
            else f"confidence_below_{CASCADE_THRESHOLD_BPS}"
        )
        reasons += [f"local:{r}" for r in ver.reasons]
        out2, ver2, err2, _ = await _try(
            gw, ModelRole.PARSER_FALLBACK, messages, norm, guess, packs
        )
        calls += 1
        if err2:
            reasons.append(f"cloud_error:{err2}")
        if ver2.schema_valid and (not ver.schema_valid or ver2.confidence_bps > ver.confidence_bps):
            chosen_out, chosen_ver, used = out2, ver2, "cloud"  # the higher confidence result wins
        elif not ver.schema_valid:
            chosen_out, chosen_ver, used = None, ver2, "none"
    log.info(
        "nlu_cascade", used=used, cascaded=cascaded, reasons=reasons, local_bps=local_conf,
        final_bps=chosen_ver.confidence_bps, language=norm.language.value,
    )  # fmt: skip
    if (
        chosen_out is None
        and guess.intent is not None
        and not blocked
        and _models_unavailable(reasons)
    ):
        # No model answered (provider down, quota, timeout, replay without a recording). The keyword classifier and
        # the slot builder work on the user's own words only, so the request can still be understood, at a confidence
        # that asks before it acts. Never used when a model answered with invalid output or the text was blocked.
        rule_out = ParserOutput(
            language=norm.language, intent=guess.intent, slots={}, confidence_bps=RULE_FALLBACK_BPS
        )
        built_r: SlotBuild = build_slots(rule_out, norm, packs)
        frame_r = make_frame(guess.intent, norm, built_r, RULE_FALLBACK_BPS, "local", packs)
        clarify_r = build_clarify(
            tuple(s.value for s in frame_r.missing_slots),
            built_r.ambiguous,
            norm.language,
            deva,
            packs,
        )
        return ParseResult(
            frame_r, norm, Verification(True, RULE_FALLBACK_BPS, reasons=("rule_fallback",)),
            CascadeDecision("rule", True, (*reasons, "rule_fallback"), local_conf, RULE_FALLBACK_BPS),
            clarify_r, built_r.ambiguous, built_r.unsupported_currency, tuple(built_r.warnings),
            latency_ms=int((time.monotonic() - started) * 1000), llm_calls=calls,
            raw_output="",
        )  # fmt: skip
    if chosen_out is None:
        return _failed_closed(
            norm, deva, packs, tuple(reasons), started, calls, False, chosen_ver, local_conf
        )
    built: SlotBuild = build_slots(chosen_out, norm, packs)
    frame = make_frame(chosen_out.intent, norm, built, chosen_ver.confidence_bps, used, packs)
    clarify: ClarifyRequest | None = build_clarify(
        tuple(s.value for s in frame.missing_slots), built.ambiguous, norm.language, deva, packs
    )  # deterministic evidence says what is missing or ambiguous: ask exactly that
    if clarify is None and chosen_ver.confidence_bps < CASCADE_THRESHOLD_BPS:
        clarify = generic_question(
            norm.language, deva, packs
        )  # still unsure after the cascade: ask, do not act
    return ParseResult(
        frame, norm, chosen_ver, CascadeDecision(used, cascaded, tuple(reasons), local_conf, chosen_ver.confidence_bps),
        clarify, built.ambiguous, built.unsupported_currency, tuple(built.warnings),
        latency_ms=int((time.monotonic() - started) * 1000), llm_calls=calls,
        raw_output=_masked_raw(chosen_out),
    )  # fmt: skip
