"""Build the ``IntentFrame`` with evidence-backed provenance (INV-4, ADR 0004).

Rules, all enforced here in code, never by the model:
- A slot value that comes from a normaliser candidate has an evidence span in the user's own text and
  is ``USER_TYPED``. ``source_ref`` records the span as ``user_text:<start>-<end>``.
- A slot value that comes only from the model, with no evidence in the text, is ``MODEL_INFERRED``
  (``source_ref`` is ``model_inferred``). Policy later treats such values as unconfirmed.
- The model's output never carries provenance (``llm_outputs`` has no such field). Where the model
  and the evidence disagree, the evidence wins.
- Ambiguity is never resolved by guessing: an ambiguous currency or several different payee
  candidates produce no slot and are reported separately so the agent asks.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from corridor.contracts.intent import IntentFrame, IntentType, SlotName
from corridor.contracts.llm_outputs import ParserOutput
from corridor.contracts.money import CurrencyCode
from corridor.contracts.provenance import Provenance, Tainted
from corridor.nlu.deadline import DeadlineStatus, resolve_deadline
from corridor.nlu.lang_pack import LangPacks
from corridor.nlu.normalise import (
    CurrencyStatus,
    DateKind,
    Normalised,
    Span,
    _match_seq,
    _tokenise,
    is_known_word,
)


@dataclass
class SlotBuild:
    """Slots plus the ambiguity and warning flags the agent needs."""

    slots: dict[SlotName, Tainted[Any]] = field(default_factory=dict)
    ambiguous: dict[str, tuple[str, ...]] = field(default_factory=dict)
    unsupported_currency: str | None = None
    warnings: list[str] = field(default_factory=list)


def _typed(value: Any, span: Span) -> Tainted[Any]:
    return Tainted[Any](
        value=value,
        provenance=Provenance.USER_TYPED,
        source_ref=f"user_text:{span.start}-{span.end}",
    )


def _inferred(value: Any) -> Tainted[Any]:
    return Tainted[Any](
        value=value, provenance=Provenance.MODEL_INFERRED, source_ref="model_inferred"
    )


MAX_FESTIVAL_LEAD_DAYS = (
    200  # "before Dashain" resolving to a date this far away is asked, not assumed
)
_RECURRENCES = frozenset({"monthly", "weekly", "biweekly"})
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _valid_iso(value: Any) -> bool:
    if not isinstance(value, str) or not _ISO.match(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _pick_amount(norm: Normalised, b: SlotBuild) -> Any | None:
    """The evidence backed amount, chosen only by deterministic rules (never by the model).

    One candidate: it. Several: the single one that has a currency attached, otherwise the
    amounts are ambiguous and the user is asked. A number such as an account fragment can never be
    selected by model output."""
    cands = [a for a in norm.amounts if a.in_range and a.amount_minor is not None]
    if len(norm.amounts) > len(cands):
        b.warnings.append("amount_out_of_range")
    if len(norm.amounts) == 1 and cands:
        return cands[0]
    if len(norm.amounts) > 1:
        with_cur = [a for a in cands if a.currency is not None]
        if len(with_cur) == 1:
            return with_cur[0]
        b.ambiguous["amount"] = tuple(a.span.text for a in norm.amounts)
    return None


def _purpose_span(norm: Normalised, purpose: str, packs: LangPacks) -> Span | None:
    """Locate ``purpose`` as whole tokens of the user's text; None if it is not there."""
    want = tuple(packs.fold(w) for w in purpose.split())
    toks = _tokenise(norm.original, packs)
    for i in range(len(toks) - len(want) + 1):
        if want and _match_seq(toks, i, want):
            a, z = toks[i].start, toks[i + len(want) - 1].end
            return Span(start=a, end=z, text=norm.original[a:z])
    return None


_PAIR_INTENTS = (IntentType.CONVERT_CURRENCY, IntentType.COMPARE_ROUTES)


def build_slots(output: ParserOutput | None, norm: Normalised, packs: LangPacks) -> SlotBuild:
    """Merge normaliser evidence (preferred) with model proposals (marked MODEL_INFERRED).

    The model never chooses between evidence candidates and never overrides evidence."""
    b = SlotBuild()
    m = dict(output.slots) if output else {}

    # amount ---------------------------------------------------------------------------
    chosen = _pick_amount(norm, b)
    model_amount = m.get(SlotName.AMOUNT)
    if chosen is not None:
        b.slots[SlotName.AMOUNT] = _typed(chosen.amount_minor, chosen.span)
    elif (
        not norm.amounts
        and isinstance(model_amount, int)
        and not isinstance(model_amount, bool)
        and 0 < model_amount < 10**8
    ):
        b.slots[SlotName.AMOUNT] = _inferred(model_amount * 100)

    # currency -------------------------------------------------------------------------
    resolved = {c.code for c in norm.currencies if c.status is CurrencyStatus.RESOLVED and c.code}
    amb = [c for c in norm.currencies if c.status is CurrencyStatus.AMBIGUOUS]
    uns = [c for c in norm.currencies if c.status is CurrencyStatus.UNSUPPORTED]
    if uns:
        b.unsupported_currency = uns[0].unsupported_code
    if len(resolved) == 1:
        code = next(iter(resolved))
        span = next(c.span for c in norm.currencies if c.code == code)
        b.slots[SlotName.CURRENCY] = _typed(code.value, span)
    elif len(resolved) > 1 and output is not None and output.intent in _PAIR_INTENTS:
        # A quote or comparison names two currencies by design (from and to); the agent reads the pair
        # from the normaliser's ordered currencies, so this is not an ambiguity to ask about.
        if chosen is not None and chosen.currency is not None and chosen.currency.code is not None:
            b.slots[SlotName.CURRENCY] = _typed(chosen.currency.code.value, chosen.currency.span)
    elif len(resolved) > 1:
        b.ambiguous["currency"] = tuple(sorted(c.value for c in resolved))
    elif amb:
        b.ambiguous["currency"] = amb[0].options
    elif not uns and isinstance(m.get(SlotName.CURRENCY), str):
        try:
            b.slots[SlotName.CURRENCY] = _inferred(CurrencyCode(str(m[SlotName.CURRENCY])).value)
        except ValueError:
            b.warnings.append("model_currency_invalid")

    # payee ----------------------------------------------------------------------------
    cands: list[tuple[str, str | None, Span]] = [
        (r.term, r.relation, r.span) for r in norm.relations
    ]
    cands += [(n.name, None, n.span) for n in norm.names]
    model_payee = m.get(SlotName.PAYEE_REF)
    # An unknown name the model read out of the text ("bob") is kept as a candidate, backed by its own
    # span in the user's text, so the agent can ask who it is. It is never kept without that evidence.
    if isinstance(model_payee, str) and model_payee.strip():
        have = {packs.fold(c[0]) for c in cands}
        for part in model_payee.split(" and "):
            name_span = _purpose_span(norm, part.strip(), packs)
            words = [packs.fold(w) for w in part.split()]
            if (
                name_span
                and words
                and packs.fold(name_span.text) not in have
                and not any(is_known_word(w, packs) for w in words)
            ):
                cands.append((name_span.text, None, name_span))
                have.add(packs.fold(name_span.text))
    distinct = {(c[1] or c[0].lower()) for c in cands}
    if len(distinct) == 1:
        text, relation, span = cands[0]
        b.slots[SlotName.PAYEE_REF] = _typed({"text": text, "relation": relation}, span)
    elif len(distinct) > 1:
        b.ambiguous["payee_ref"] = tuple(
            dict.fromkeys(c[0] for c in cands)
        )  # the model never picks
    elif isinstance(model_payee, str) and model_payee.strip():
        b.slots[SlotName.PAYEE_REF] = _inferred(
            {"text": model_payee.strip()[:120], "relation": None}
        )

    # deadline -------------------------------------------------------------------------
    today = norm.today
    extra = resolve_deadline(norm.original, today, norm=None) if today else None
    if extra is not None and extra.status is DeadlineStatus.NEEDS_CLARIFICATION:
        b.ambiguous["deadline"] = ()  # a phrase we cannot resolve is asked about, never guessed
        b.warnings.append(f"deadline_{extra.reason}")
    elif extra is not None and extra.status is DeadlineStatus.RESOLVED and extra.on and extra.at:
        at = norm.original.lower().find((extra.phrase or "").lower())
        a = max(at, 0)
        span = Span(
            start=a,
            end=a + len(extra.phrase or ""),
            text=norm.original[a : a + len(extra.phrase or "")],
        )
        b.slots[SlotName.DEADLINE] = _typed(
            {"kind": extra.kind, "date": extra.on.isoformat(), "at": extra.at.isoformat()}, span
        )
    handled = extra is not None and extra.status is not DeadlineStatus.NONE
    fest = next(iter(norm.festivals), None)
    dated = [d for d in norm.dates if d.date is not None and not d.is_past]
    distinct_dates = {d.date for d in dated}
    past_relative = [
        d
        for d in norm.dates
        if d.date is not None and d.is_past and d.kind is DateKind.RELATIVE_DAY
    ]
    if handled:
        pass
    elif fest is not None:
        far = bool(fest.date and today and (fest.date - today).days > MAX_FESTIVAL_LEAD_DAYS)
        if far and fest.date:
            b.ambiguous["deadline"] = (fest.date.isoformat(),)
            b.warnings.append("festival_far_future")
        else:
            value: dict[str, Any] = {
                "kind": fest.deadline_kind, "festival": fest.festival_id,
                "date": fest.date.isoformat() if fest.date else None, "verified": fest.verified,
                "source_grade": fest.source_grade,
            }  # fmt: skip
            if fest.conflicting_dates:
                value["conflicting_sources"] = True
                value["other_source_dates"] = [cd.isoformat() for cd in fest.conflicting_dates]
            b.slots[SlotName.DEADLINE] = _typed(value, fest.span)
            if fest.date is None:
                b.warnings.append("festival_date_unknown")
    elif len(distinct_dates) > 1:
        b.ambiguous["deadline"] = tuple(sorted(d.isoformat() for d in distinct_dates if d))
    elif dated:
        d = dated[0]
        b.slots[SlotName.DEADLINE] = _typed(
            {"kind": "date", "date": d.date.isoformat() if d.date else None}, d.span
        )
        if d.ambiguous:
            b.warnings.append("date_order_ambiguous")
        if d.rolled_year:
            b.warnings.append("date_rolled_to_next_year")
    elif past_relative:
        # "by yesterday": keep the date so the agent says it has passed and asks what to do (never dropped)
        d = past_relative[0]
        b.slots[SlotName.DEADLINE] = _typed(
            {"kind": "date", "date": d.date.isoformat() if d.date else None}, d.span
        )
    elif _valid_iso(m.get(SlotName.DEADLINE)):
        b.slots[SlotName.DEADLINE] = _inferred({"kind": "date", "date": str(m[SlotName.DEADLINE])})
    elif m.get(SlotName.DEADLINE) is not None:
        b.warnings.append("model_deadline_invalid")

    # recurrence and condition -------------------------------------------------------------
    model_rec = m.get(SlotName.RECURRENCE)
    if norm.recurrences:
        r = norm.recurrences[0]
        b.slots[SlotName.RECURRENCE] = _typed({"freq": r.freq}, r.span)
    elif isinstance(model_rec, str) and model_rec in _RECURRENCES:
        b.slots[SlotName.RECURRENCE] = _inferred({"freq": model_rec})
    elif model_rec is not None:
        b.warnings.append("model_recurrence_invalid")
    if norm.conditions:
        c = norm.conditions[0]
        b.slots[SlotName.CONDITION] = _typed(
            {"kind": c.kind, "reserve": c.reserve, "op": c.op, "target": c.target}, c.span
        )

    # purpose: USER_TYPED only if it is whole tokens the user wrote, and the stored value is the
    # user's own text slice, never the model's string ------------------------------------------
    purpose = m.get(SlotName.PURPOSE)
    if isinstance(purpose, str) and purpose.strip():
        pv = purpose.strip()[:120]
        purpose_span = _purpose_span(norm, pv, packs)
        if purpose_span is not None:
            b.slots[SlotName.PURPOSE] = _typed(purpose_span.text, purpose_span)
        else:
            b.slots[SlotName.PURPOSE] = _inferred(pv)
    return b


def required_missing(
    intent: IntentType, build: SlotBuild, packs: LangPacks
) -> tuple[SlotName, ...]:
    """Required slots (data/nlu/slots.yaml) that have no value and are not already ambiguous."""
    need = packs.required_slots.get(intent.value, ())
    return tuple(
        SlotName(s) for s in need if SlotName(s) not in build.slots and s not in build.ambiguous
    )


def make_frame(
    intent: IntentType,
    norm: Normalised,
    build: SlotBuild,
    confidence_bps: int,
    parser: str,
    packs: LangPacks,
) -> IntentFrame:
    """Assemble the IntentFrame contract (slots, missing slots, confidence, parser label)."""
    missing = required_missing(intent, build, packs)
    return IntentFrame(
        language=norm.language,
        intent=intent,
        slots=build.slots,
        missing_slots=missing,
        confidence_bps=confidence_bps,
        parser="local" if parser == "local" else "cloud",
    )
