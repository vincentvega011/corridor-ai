"""Generate fine-tuning data for the local parser (docs/spec/modules.md 5.4).

    cd backend && uv run python ../models/finetune/gen_data.py [--target 4000] [--batch 8]
    cd backend && uv run python ../models/finetune/gen_data.py --status

Label first, then write: the script samples a structured request (intent, slots, persona, language
style, noise kind) with a fixed RNG seed, and asks the cloud model (role ``parser_fallback``) to WRITE a
realistic message that expresses exactly that request. The label is never taken from the model:
every generated message must pass deterministic checks or it is rejected:

1. schema: well formed batch, text 3 to 300 characters, no digit run of 9 or more, no URL;
2. agreement with the normaliser: the gold amount, currency, payee, deadline, recurrence and condition
   must be what the normaliser extracts from the generated text, and the keyword classifier must not
   contradict the gold intent (so the training target equals what the verifier would accept);
3. dedupe: exact (normalised) and near duplicate (character 4-gram Jaccard);
4. leakage guard: any text too similar to eval/nlu/candidates.jsonl or test.jsonl is dropped.

Resumable: ``models/finetune/data/checkpoint.json`` stores the next spec index; accepted and rejected
rows are appended to ``raw_accepted.jsonl`` and ``rejected.jsonl``. Spec ``i`` is a pure function of
``(seed, i)``, so resuming never repeats or skips work. Rate: at most 30 requests a minute and
about 1,000 an hour against the Token Harbor limiter (limits 50 and 1,500), and the script sleeps
instead of ever raising QuotaExhausted. Output: ``train.jsonl`` and ``valid.jsonl`` in the mlx-lm
chat format (system, user, assistant messages built exactly as the gateway and parser build them).
All data is synthetic.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import random
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from corridor.contracts.llm_outputs import ParserOutput
from corridor.llm import Gateway, LLMError, ModelRole, QuotaExhausted
from corridor.llm.client import _schema_instruction
from corridor.nlu.lang_pack import load_packs
from corridor.nlu.normalise import CurrencyStatus, normalise
from corridor.nlu.parser import redact_long_numbers, user_message
from corridor.nlu.rules import classify_intent
from corridor.nlu.slots import build_slots

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DATA = HERE / "data"
TODAY = date(2026, 10, 10)
SEED = 20261010
log = logging.getLogger("gen_data")

STYLE_DESC = {
    "en": "English",
    "ne": "Nepali written in Devanagari script",
    "ne_latn": "Nepali written in Latin letters (romanised, the way people type on a phone)",
    "mixed": "a natural mix of Nepali (Latin letters) and English words in one message",
}
STYLE_W = [("ne_latn", 40), ("en", 20), ("ne", 20), ("mixed", 20)]
NOISE_DESC = {
    "none": "clean, natural wording",
    "typos": "a couple of realistic typing mistakes or transposed letters",
    "vowels": "missing vowels and shortened words as in fast phone typing (for example hjr, pthau)",
    "abbrev": "chat abbreviations such as pls, plz, thx, asap",
    "emoji": "one or two emojis",
    "voice": "voice-to-text style: all lowercase, no punctuation, run-on, slight mishearings",
    "mixedscript": "switch between Devanagari and Latin letters inside the message",
    "polite": "very polite and wordy",
}
NOISE_W = [
    ("none", 35),
    ("typos", 12),
    ("vowels", 12),
    ("abbrev", 10),
    ("emoji", 8),
    ("voice", 10),
    ("mixedscript", 5),
    ("polite", 8),
]
PERSONAS: dict[str, dict[str, Any]] = {
    "ramesh": {
        "who": "a migrant worker in Doha who sends money home to Nepal",
        "ccy": ["QAR", "NPR"],
        "payees": [
            "mother",
            "father",
            "elder_brother",
            "elder_sister",
            "younger_brother",
            "landlord",
            "Suresh",
            "Hari",
        ],
        "amounts": [
            200,
            300,
            500,
            800,
            1000,
            1500,
            2000,
            5000,
            10000,
            15000,
            20000,
            25000,
            30000,
            50000,
            100000,
            150000,
        ],
    },
    "priya": {
        "who": "a Nepali student in Sydney who pays rent and university tuition in AUD",
        "ccy": ["AUD", "NPR"],
        "payees": ["university", "landlord", "father", "mother", "Sita"],
        "amounts": [100, 200, 500, 800, 1200, 1800, 3000, 6500, 5000, 20000, 50000],
    },
    "anil": {
        "who": "a Nepali student in China who pays rent and tuition in CNY and gets money from parents",
        "ccy": ["CNY", "NPR"],
        "payees": ["university", "landlord", "father", "mother", "Binod"],
        "amounts": [300, 500, 1000, 2500, 5000, 18000, 20000, 50000],
    },
}
INTENT_W = [
    ("send_money", 40),
    ("schedule_transfer", 8),
    ("pay_invoice", 10),
    ("check_balance", 8),
    ("compare_routes", 6),
    ("convert_currency", 5),
    ("set_budget", 3),
    ("spending_summary", 4),
    ("add_payee", 3),
    ("credit_passport_issue", 2),
    ("credit_passport_share", 2),
    ("explain_decision", 3),
    ("cancel_schedule", 3),
    ("help", 2),
    ("other", 3),
]
DEADLINES = [
    ("festival", "before Dashain"), ("festival", "before Tihar"), ("festival", "for Chhath"), ("festival", "before Nepali New Year"),
    ("relative", "today"), ("relative", "tomorrow"), ("relative", "the day after tomorrow"),
    ("weekday", "by Friday"), ("weekday", "by next Monday"), ("explicit", "by 31 October"), ("explicit", "by 5 November"), ("explicit", "by 2026-11-15"),
]  # fmt: skip
NAMES = {"Suresh", "Hari", "Sita", "Binod"}


def pick(rng: random.Random, weighted: list[tuple[str, int]]) -> str:
    return rng.choices([k for k, _ in weighted], [w for _, w in weighted])[0]


def make_spec(i: int) -> dict[str, Any]:
    """Spec ``i`` as a pure function of (SEED, i)."""
    rng = random.Random(SEED * 1_000_003 + i)
    persona = rng.choice(sorted(PERSONAS))
    p = PERSONAS[persona]
    intent = pick(rng, INTENT_W)
    style = pick(rng, STYLE_W)
    noise = pick(rng, NOISE_W)
    gold: dict[str, Any] = {}
    needs_amount = intent in {
        "send_money",
        "schedule_transfer",
        "pay_invoice",
        "convert_currency",
        "compare_routes",
        "set_budget",
    }
    if needs_amount and (intent != "pay_invoice" or rng.random() < 0.5) and rng.random() < 0.88:
        gold["amount"] = rng.choice(p["amounts"])
        c = rng.random()
        if c < 0.45:
            gold["currency"] = rng.choice(p["ccy"])
        elif c < 0.55 and intent == "send_money":
            gold["currency"] = "ambiguous_dollar"
    if (
        intent
        in {
            "send_money",
            "schedule_transfer",
            "add_payee",
            "cancel_schedule",
            "credit_passport_share",
        }
        and rng.random() < 0.9
    ):
        gold["payee"] = rng.choice(p["payees"])
    if intent == "pay_invoice" and rng.random() < 0.7:
        gold["payee"] = "university"
    if intent in {"send_money", "pay_invoice"} and rng.random() < 0.3:
        gold["deadline"] = rng.choice(DEADLINES)
    if intent == "schedule_transfer" or (
        intent in {"cancel_schedule", "set_budget"} and rng.random() < 0.5
    ):
        gold["recurrence"] = rng.choice(["monthly", "monthly", "weekly"])
    if intent == "send_money" and rng.random() < 0.1:
        gold["condition"] = "rent"
    return {
        "i": i,
        "persona": persona,
        "intent": intent,
        "style": style,
        "noise": noise,
        "gold": gold,
    }


def describe(spec: dict[str, Any]) -> str:
    """The structured request in words for the writer model."""
    p = PERSONAS[spec["persona"]]
    g = spec["gold"]
    parts = [f"intent: {spec['intent']}"]
    if "amount" in g:
        cur = g.get("currency")
        if cur == "ambiguous_dollar":
            parts.append(
                f"amount: {g['amount']} written with the bare word 'dollar' (never AUD or USD)"
            )
        elif cur:
            parts.append(f"amount: {g['amount']} {cur} (the message must name the currency)")
        else:
            parts.append(
                f"amount: {g['amount']} (do NOT name any currency or say rupees, riyal, dollar)"
            )
        parts.append(
            "write the amount the way people say it (20000 may be 20 hajar, 20k, 20,000, bis hajar, or in Devanagari digits and words)"
        )
    if "payee" in g:
        who = g["payee"]
        parts.append(
            f"recipient: {who} "
            + (
                "(a name, spell it exactly)"
                if who in NAMES
                else "(use a natural term for this relation, or the word as the relative is addressed)"
            )
        )
    if "deadline" in g:
        parts.append(f"deadline: {g['deadline'][1]}")
    if "recurrence" in g:
        parts.append(f"repeats: every {'month' if g['recurrence'] == 'monthly' else 'week'}")
    if "condition" in g:
        parts.append("condition: only if the rent is covered first")
    return "; ".join(parts) + f". Writer persona: {p['who']}."


def canonical_deadline(kind_phrase: tuple[str, str]) -> str | None:
    """The date the normaliser gives for the canonical English phrase (the gold label)."""
    n = normalise(kind_phrase[1], TODAY)
    if n.festivals and n.festivals[0].date:
        return n.festivals[0].date.isoformat()
    if n.dates and n.dates[0].date:
        return n.dates[0].date.isoformat()
    return None


# ------------------------------------------------------------------------------- validation
def squash(text: str) -> str:
    return re.sub(r"[^\wऀ-ॿ]+", " ", text.lower()).strip()


def shingles(text: str, n: int = 4) -> set[str]:
    s = squash(text).replace(" ", "_")
    return {s[i : i + n] for i in range(max(1, len(s) - n + 1))}


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


class GenItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    text: str = Field(min_length=1, max_length=400)


class GenBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[GenItem]


def validate(spec: dict[str, Any], text: str) -> tuple[dict[str, Any] | None, str | None]:
    """Return (training target slots dict, None) or (None, rejection reason)."""
    packs = load_packs()
    text = text.strip()
    if not 3 <= len(text) <= 300:
        return None, "bad_length"
    if re.search(r"https?://|www\.", text):
        return None, "url"
    if redact_long_numbers(text) != text:
        return None, "long_number"
    norm = normalise(text, TODAY, packs)
    g = spec["gold"]
    guess = classify_intent(text, norm, packs)
    if guess.intent is not None and guess.intent.value != spec["intent"]:
        if not (
            spec["intent"] in {"schedule_transfer", "send_money"}
            and guess.intent.value in {"schedule_transfer", "send_money"}
        ):
            return None, "intent_contradiction"
    # amount
    in_range = [a for a in norm.amounts if a.in_range]
    if "amount" in g:
        if [int(a.units) for a in in_range if "." not in a.units] != [g["amount"]]:
            return None, "amount_disagrees"
    elif norm.amounts:
        return None, "unexpected_amount"
    # currency
    resolved = {
        c.code.value for c in norm.currencies if c.status is CurrencyStatus.RESOLVED and c.code
    }
    amb = any(c.status is CurrencyStatus.AMBIGUOUS for c in norm.currencies)
    cur = g.get("currency")
    if cur == "ambiguous_dollar":
        if not amb or resolved:
            return None, "currency_disagrees"
    elif cur:
        if resolved != {cur}:
            return None, "currency_disagrees"
    elif resolved or amb or any(c.status is CurrencyStatus.UNSUPPORTED for c in norm.currencies):
        return None, "unexpected_currency"
    # payee
    payee = g.get("payee")
    if payee:
        rel = {r.relation for r in norm.relations}
        names = {n.name.lower() for n in norm.names}
        if payee in NAMES:
            if payee.lower() not in names:
                return None, "payee_disagrees"
        elif payee not in rel:
            return None, "payee_disagrees"
    # deadline
    if "deadline" in g:
        want = canonical_deadline(tuple(g["deadline"]))
        got = {f.date.isoformat() for f in norm.festivals if f.date} | {
            d.date.isoformat() for d in norm.dates if d.date
        }
        if want is None or want not in got:
            return None, "deadline_disagrees"
    elif norm.festivals:
        return None, "unexpected_deadline"
    if "recurrence" in g:
        if g["recurrence"] not in {r.freq for r in norm.recurrences}:
            return None, "recurrence_disagrees"
    elif norm.recurrences and spec["intent"] != "schedule_transfer":
        return None, "unexpected_recurrence"
    if ("condition" in g) != bool(norm.conditions):
        return None, "condition_disagrees"
    # the training target: what the evidence says, with the gold intent
    from corridor.contracts.intent import IntentType as IT

    build = build_slots(None, norm, packs)
    slots: dict[str, Any] = {}
    for k, v in build.slots.items():
        val = v.value
        if k.value == "amount" and isinstance(val, int) and norm.amounts[0].in_range:
            slots["amount"] = (
                int(norm.amounts[0].units) if "." not in norm.amounts[0].units else None
            )
        elif k.value == "currency":
            slots["currency"] = val
        elif k.value == "payee_ref":
            slots["payee_ref"] = val["text"]
        elif k.value == "deadline" and val.get("date"):
            slots["deadline"] = val["date"]
        elif k.value == "recurrence":
            slots["recurrence"] = val["freq"]
        elif k.value == "condition":
            slots["condition"] = f"only if {val['reserve']} is covered"
    slots = {k: v for k, v in slots.items() if v is not None}
    need = packs.required_slots.get(IT(spec["intent"]).value, ())
    missing = [s for s in need if s not in slots and s not in build.ambiguous]
    out = {
        "language": norm.language.value,
        "intent": spec["intent"],
        "slots": slots,
        "missing_slots": missing,
        "confidence_bps": 9500,
    }
    ParserOutput.model_validate(out)  # the target itself must be schema valid
    return out, None


# ------------------------------------------------------------------------------- run
def load_held() -> list[tuple[str, set[str]]]:
    """Normalised text and shingles of every evaluation item (candidates, test, synthetic, human).

    candidates.jsonl must exist: without it the leakage guard would silently do nothing."""
    held: list[str] = []
    cand = REPO / "eval" / "nlu" / "candidates.jsonl"
    if not cand.exists():
        raise FileNotFoundError(
            f"{cand} is missing: refusing to generate without the leakage guard"
        )
    for name in ("candidates.jsonl", "test.jsonl", "synthetic.jsonl", "human.jsonl", "human_review.jsonl"):
        f = REPO / "eval" / "nlu" / name
        if f.exists():
            held += [
                json.loads(x)["text"]
                for x in f.read_text(encoding="utf-8").splitlines()
                if x.strip()
            ]
    return [(squash(t), shingles(t)) for t in held]


def leaks(text: str, held: list[tuple[str, set[str]]]) -> bool:
    key, sh = squash(text), shingles(text)
    return any(key == hk or jaccard(sh, hs) >= 0.6 for hk, hs in held)


class State:
    def __init__(self) -> None:
        DATA.mkdir(exist_ok=True)
        self.ckpt = DATA / "checkpoint.json"
        self.accepted = DATA / "raw_accepted.jsonl"
        self.rejected = DATA / "rejected.jsonl"
        d = json.loads(self.ckpt.read_text()) if self.ckpt.exists() else {}
        self.next_i: int = d.get("next_i", 0)
        self.reasons: dict[str, int] = d.get("reasons", {})
        self.calls: int = d.get("calls", 0)
        self.rows: list[dict[str, Any]] = (
            [
                json.loads(x)
                for x in self.accepted.read_text(encoding="utf-8").splitlines()
                if x.strip()
            ]
            if self.accepted.exists()
            else []
        )
        self.seen = {squash(r["text"]) for r in self.rows}
        self.shingles = [shingles(r["text"]) for r in self.rows]
        self.held = load_held()

    def save(self) -> None:
        self.ckpt.write_text(
            json.dumps(
                {
                    "next_i": self.next_i,
                    "reasons": self.reasons,
                    "calls": self.calls,
                    "accepted": len(self.rows),
                    "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                },
                indent=1,
            )
        )

    def reject(self, spec: dict[str, Any], text: str, reason: str) -> None:
        self.reasons[reason] = self.reasons.get(reason, 0) + 1
        with self.rejected.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps({"i": spec["i"], "reason": reason, "text": text}, ensure_ascii=False)
                + "\n"
            )

    def accept(self, spec: dict[str, Any], text: str, target: dict[str, Any]) -> str | None:
        key = squash(text)
        if key in self.seen:
            return "duplicate"
        sh = shingles(text)
        if leaks(text, self.held):
            return "leakage"
        if any(jaccard(sh, o) >= 0.9 for o in self.shingles):
            return "near_duplicate"
        row = {
            "i": spec["i"],
            "text": text.strip(),
            "style": spec["style"],
            "noise": spec["noise"],
            "persona": spec["persona"],
            "target": target,
        }
        self.rows.append(row)
        self.seen.add(key)
        self.shingles.append(sh)
        with self.accepted.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return None


SYSTEM = (
    "You write realistic chat messages that a customer of a cross-border money app might type. "
    "For each numbered request, write ONE message that expresses exactly that request in the stated language style and noise. "
    "Rules: never include account numbers, phone numbers or long digit strings; use only fictional names; keep each message under 200 characters; "
    "do not explain; do not add extra amounts, currencies or dates that the request does not mention. "
    'Return JSON: {"messages": [{"id": <request number>, "text": "<the message>"}]}.'
)


def batch_prompt(specs: list[dict[str, Any]]) -> list[dict[str, str]]:
    lines = []
    for s in specs:
        lines.append(
            f"{s['i']}. Style: {STYLE_DESC[s['style']]}. Noise: {NOISE_DESC[s['noise']]}. Request: {describe(s)}"
        )
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n".join(lines)}]


async def throttle(gw: Gateway) -> None:
    """Stay well below the Token Harbor limits (30 a minute, 1,000 an hour) and never trip QuotaExhausted."""
    while True:
        per_min, per_hour = gw.limiter.usage("tokenharbor")
        if per_min < 30 and per_hour < 1000:
            return
        log.info("throttle: %s/min %s/hour, sleeping", per_min, per_hour)
        await asyncio.sleep(10 if per_min >= 30 else 120)


def build_example(row: dict[str, Any]) -> dict[str, Any]:
    packs = load_packs()
    norm = normalise(row["text"], TODAY, packs)
    system = (
        (REPO / "backend/src/corridor/nlu/prompts/parser_v1.txt")
        .read_text(encoding="utf-8")
        .strip()
    )
    msgs = [
        _schema_instruction(ParserOutput),
        {"role": "system", "content": system},
        {"role": "user", "content": user_message(row["text"], norm)},
        {"role": "assistant", "content": json.dumps(row["target"], ensure_ascii=False)},
    ]
    return {"messages": msgs}


_RENT = re.compile(r"rent|bhada|bhāḍā|भाडा|landlord|makan ?malik|घरधनी|घरबेटी", re.IGNORECASE)
_NAMES_INVOICE = re.compile(
    r"invoice|invois|bill|tuition|tution|\bfees?\b|शुल्क|फि\b|इनभ्वाइस|इन्भ्वाइस|बिल|ट्युसन|ट्यूसन|bijak|बिजक",
    re.IGNORECASE,
)


def apply_rent_rule(text: str, target: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Parser prompt rule (P2-3): pay_invoice is only for an invoice or bill the message names as one;
    paying rent or a person is send_money. Deterministic: a pay_invoice row that mentions rent (or a
    landlord) and does not name an invoice, bill, tuition or fee becomes send_money, and its missing
    slots are recomputed from the slots the row already has. Nothing else is touched."""
    if target.get("intent") != "pay_invoice" or not _RENT.search(text) or _NAMES_INVOICE.search(text):
        return target, False
    slots = target.get("slots", {})
    return {
        **target,
        "intent": "send_money",
        "missing_slots": [s for s in ("amount", "payee_ref") if s not in slots],
    }, True


def write_splits(st: State) -> None:
    """Rebuild train and valid from accepted rows, re-validating each one against the CURRENT
    normaliser and the CURRENT evaluation files (items approved after generation cannot leak)."""
    held = load_held()
    train, valid = [], []
    dropped: dict[str, int] = {}
    relabelled: list[str] = []
    for row in st.rows:
        try:
            target, reason = validate(make_spec(row["i"]), row["text"])
        except Exception as exc:
            target, reason = None, f"validator_error:{type(exc).__name__}"
        if target is None:
            dropped[reason or "invalid"] = dropped.get(reason or "invalid", 0) + 1
            continue
        if leaks(row["text"], held):
            dropped["leakage"] = dropped.get("leakage", 0) + 1
            continue
        target, changed = apply_rent_rule(row["text"], target)
        if changed:
            relabelled.append(row["text"])
        row = {**row, "target": target}
        bucket = int(hashlib.sha256(str(row["i"]).encode()).hexdigest(), 16) % 100
        (valid if bucket < 5 else train).append(build_example(row))
    for name, rows in (("train", train), ("valid", valid)):
        (DATA / f"{name}.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
        )
    sample = [json.dumps(r, ensure_ascii=False) for r in train[:50]]
    (HERE / "sample_50.jsonl").write_text("\n".join(sample) + "\n", encoding="utf-8")
    log.info("wrote %d train, %d valid, dropped on rebuild: %s", len(train), len(valid), dropped)
    log.info("rent rule: relabelled %d rows pay_invoice -> send_money", len(relabelled))
    for t in relabelled:
        log.info("  relabelled: %s", t)


def status() -> None:
    st = State()
    total = st.next_i
    rej = sum(st.reasons.values())
    print(
        json.dumps(
            {
                "accepted": len(st.rows),
                "specs_done": total,
                "rejected": rej,
                "reject_rate_pct": round(100 * rej / max(1, len(st.rows) + rej), 1),
                "reasons": dict(sorted(st.reasons.items(), key=lambda kv: -kv[1])),
                "llm_calls": st.calls,
            },
            indent=1,
        )
    )


async def run(target: int, batch: int) -> None:
    st = State()
    gw = Gateway()
    started, start_accepted = time.monotonic(), len(st.rows)
    failures = 0
    try:
        while len(st.rows) < target:
            specs = [make_spec(i) for i in range(st.next_i, st.next_i + batch)]
            await throttle(gw)
            try:
                res = await gw.complete(
                    ModelRole.PARSER_FALLBACK,
                    batch_prompt(specs),
                    schema=GenBatch,
                    provider="tokenharbor",
                    max_tokens=4000,
                    timeout_s=120,
                )
                st.calls += 1
                failures = 0
            except QuotaExhausted as exc:
                wait = (exc.retry_after_s or 60) + 5
                log.warning(
                    "quota exhausted: sleeping %ss (the generator keeps below the limit; other traffic used it)",
                    wait,
                )
                await asyncio.sleep(wait)
                continue
            except LLMError as exc:
                failures += 1
                log.warning(
                    "batch %s failed: %s (%d in a row)", st.next_i, type(exc).__name__, failures
                )
                if failures >= 50:
                    log.error("too many failures, stopping")
                    break
                await asyncio.sleep(min(300, 5 * failures))
                if failures % 3 == 0:  # skip a batch that keeps failing
                    st.next_i += batch
                    st.save()
                continue
            by_id = (
                {m.id: m.text for m in res.parsed.messages}
                if isinstance(res.parsed, GenBatch)
                else {}
            )
            for s in specs:
                text = by_id.get(s["i"])
                if text is None:
                    st.reject(s, "", "missing_in_batch")
                    continue
                try:
                    target_row, reason = validate(s, text)
                except Exception as exc:
                    target_row, reason = None, f"validator_error:{type(exc).__name__}"
                if target_row is None:
                    st.reject(s, text, reason or "invalid")
                    continue
                dup = st.accept(s, text, target_row)
                if dup:
                    st.reject(s, text, dup)
            st.next_i += batch
            st.save()
            got = len(st.rows) - start_accepted
            elapsed = time.monotonic() - started
            rate = got / elapsed * 3600 if elapsed else 0
            remaining = (target - len(st.rows)) / rate * 3600 if rate else 0
            log.info(
                "accepted %d/%d (spec %d) rate %.0f/hour, about %.0f min left",
                len(st.rows),
                target,
                st.next_i,
                rate,
                remaining / 60,
            )
        write_splits(st)
    finally:
        await gw.aclose()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout
    )
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=4000)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--status", action="store_true")
    ap.add_argument(
        "--splits-only", action="store_true", help="rebuild train/valid from raw_accepted.jsonl"
    )
    args = ap.parse_args()
    if args.status:
        status()
    elif args.splits_only:
        write_splits(State())
    else:
        asyncio.run(run(args.target, args.batch))


if __name__ == "__main__":
    main()
