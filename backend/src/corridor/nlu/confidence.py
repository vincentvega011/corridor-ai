"""Verifier based confidence (ADR 0004). The model's own confidence is never trusted.

``confidence_bps`` is computed from deterministic checks of the model output against the
normaliser's evidence and the keyword classifier:

    confidence_bps = 0                                   if the output is not schema valid
    otherwise  min( 10000 - 500*abstain - 1500*soft ,  6000 if hard > 0 else 10000 )

- schema valid: the gateway returned a ``ParserOutput`` (after at most one repair retry).
- abstain (1 if the keyword classifier has no opinion, else 0): neutral, costs 500.
- hard failures (each caps the result at 6000, so the cascade always triggers):
    intent_disagrees, amount_mismatch (also an amount with no evidence in the text),
    currency_conflict (a currency that contradicts evidence, or one chosen for an ambiguous word).
- soft failures (1500 each): currency_without_evidence, deadline_mismatch (also a deadline with no
  evidence), payee_ungrounded (a payee reference that is not in the text), recurrence_without_evidence,
  condition_without_evidence.

Boundaries: all pass 10000; abstain 9500; one soft 8500; abstain + one soft 8000 (no cascade);
two soft 7000 (cascade); any hard 6000 or less (cascade). The cascade threshold is 8000.
"""

from dataclasses import dataclass, field

from corridor.contracts.intent import SlotName
from corridor.contracts.llm_outputs import ParserOutput
from corridor.contracts.money import CurrencyCode
from corridor.nlu.lang_pack import LangPacks
from corridor.nlu.normalise import CurrencyStatus, Normalised
from corridor.nlu.rules import IntentGuess, compatible

CASCADE_THRESHOLD_BPS = 8000
FULL_BPS = 10000
ABSTAIN_PENALTY_BPS = 500
SOFT_PENALTY_BPS = 1500
HARD_CAP_BPS = 6000


@dataclass(frozen=True)
class Verification:
    """Outcome of verifying one model output."""

    schema_valid: bool
    confidence_bps: int
    hard: tuple[str, ...] = ()
    soft: tuple[str, ...] = ()
    abstained: bool = False
    reasons: tuple[str, ...] = field(default=())

    @property
    def needs_cascade(self) -> bool:
        """True when the local result must not be trusted (threshold or invalid)."""
        return (not self.schema_valid) or self.confidence_bps < CASCADE_THRESHOLD_BPS


def score(schema_valid: bool, hard: int, soft: int, abstained: bool) -> int:
    """The confidence formula (kept separate so the boundaries can be tested directly)."""
    if not schema_valid:
        return 0
    value = FULL_BPS - ABSTAIN_PENALTY_BPS * int(abstained) - SOFT_PENALTY_BPS * soft
    cap = HARD_CAP_BPS if hard > 0 else FULL_BPS
    return max(0, min(value, cap))


def _folded(text: str, packs: LangPacks) -> str:
    return " ".join(packs.fold(w) for w in text.split())


def verify(
    output: ParserOutput | None, norm: Normalised, guess: IntentGuess, packs: LangPacks
) -> Verification:
    """Verify a parser output against the evidence. ``output`` None means schema invalid."""
    if output is None:
        return Verification(False, 0, reasons=("schema_invalid",))
    hard: list[str] = []
    soft: list[str] = []
    slots = output.slots

    if not compatible(output.intent, guess, norm):
        hard.append("intent_disagrees")

    amount = slots.get(_slot("amount"))
    evidence_units = {int(a.units) for a in norm.amounts if a.in_range and "." not in a.units}
    amount_ok = (
        isinstance(amount, int) and not isinstance(amount, bool) and amount in evidence_units
    )
    if amount is not None and not amount_ok:
        hard.append("amount_mismatch")

    currency = slots.get(_slot("currency"))
    resolved = {c.code for c in norm.currencies if c.status is CurrencyStatus.RESOLVED and c.code}
    ambiguous = any(c.status is CurrencyStatus.AMBIGUOUS for c in norm.currencies)
    if currency is not None:
        try:
            code: CurrencyCode | None = CurrencyCode(str(currency))
        except ValueError:
            code = None
        unsupported = any(c.status is CurrencyStatus.UNSUPPORTED for c in norm.currencies)
        if (
            code is None
            or (resolved and code not in resolved)
            or (ambiguous and not resolved)
            or (unsupported and not resolved)
        ):
            hard.append("currency_conflict")
        elif not resolved:
            soft.append("currency_without_evidence")

    deadline = slots.get(_slot("deadline"))
    if deadline is not None:
        dates = {str(d.date) for d in norm.dates if d.date} | {
            str(f.date) for f in norm.festivals if f.date
        }
        if str(deadline) not in dates:
            soft.append("deadline_mismatch")

    payee = slots.get(_slot("payee_ref"))
    if isinstance(payee, str) and payee.strip():
        text_f = _folded(norm.original, packs)
        evidence = {packs.fold(r.term) for r in norm.relations} | {
            packs.fold(r.relation) for r in norm.relations
        }
        evidence |= {packs.fold(n.name) for n in norm.names}
        if packs.fold(payee.strip()) not in evidence and _folded(payee, packs) not in text_f:
            soft.append("payee_ungrounded")

    if slots.get(_slot("recurrence")) is not None and not norm.recurrences:
        soft.append("recurrence_without_evidence")
    if slots.get(_slot("condition")) is not None and not norm.conditions:
        soft.append("condition_without_evidence")

    abstained = guess.intent is None
    conf = score(True, len(hard), len(soft), abstained)
    return Verification(True, conf, tuple(hard), tuple(soft), abstained, tuple([*hard, *soft]))


def _slot(name: str) -> SlotName:
    return SlotName(name)
