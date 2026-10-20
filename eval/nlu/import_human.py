"""Turn a filled human sheet into NLU test items for owner review (P3-4).

    python3 eval/nlu/import_human.py import  eval/nlu/human_filled.csv     # writes eval/nlu/human_review.jsonl
    python3 eval/nlu/import_human.py accept                                # reviewed items -> eval/nlu/human.jsonl

The sheet (``human_template.csv``) has the columns ``message, meaning, language``. ``message`` is what a person would
type, ``meaning`` says in plain English what they want, ``language`` is one of en, ne, ne_latn, mixed. Rows that start
with ``#`` (the examples) and empty rows are skipped.

``import`` proposes labels from the MEANING text with fixed rules (never from the parser under test, which would be
circular): an intent from key words, an amount in minor units, a currency, a payee relation and a deadline. Anything
the rules cannot decide is left empty for the reviewer. Every item is written with ``"reviewed": false``,
``"human_written": true`` and the proposal under ``"proposed"``; nothing becomes a gold label until the owner sets
``"reviewed": true`` (and fixes ``intent`` and ``slots``) in ``human_review.jsonl`` and runs ``accept``.

Checks: language is known, the message is not empty or longer than 300 characters, no run of six or more digits
(account-like numbers never go into evaluation data), and the message is not a near copy of an existing evaluation item.
Human items are always reported separately from ``synthetic.jsonl`` and are never training data.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
TODAY = date(2026, 10, 10)
LANGS = ("en", "ne", "ne_latn", "mixed")
CURRENCIES = {"npr": "NPR", "rupee": "NPR", "qar": "QAR", "riyal": "QAR", "aud": "AUD", "cny": "CNY", "yuan": "CNY"}
RELATIONS = (
    ("elder brother", "elder_brother"), ("younger brother", "younger_brother"), ("elder sister", "elder_sister"),
    ("younger sister", "younger_sister"), ("mother", "mother"), ("father", "father"), ("landlord", "landlord"),
    ("university", "university"),
)  # fmt: skip
INTENT_WORDS = (
    ("cancel_schedule", ("cancel the monthly", "cancel my monthly", "stop the monthly", "cancel the schedule")),
    ("schedule_transfer", ("every month", "monthly", "each month", "recurring", "weekly")),
    ("pay_invoice", ("invoice", "tuition", "fee")),
    ("compare_routes", ("cheapest", "compare", "best route", "which provider")),
    ("convert_currency", ("convert", "exchange rate", "worth in")),
    ("add_payee", ("add a payee", "new payee", "save a payee", "add payee")),
    ("check_balance", ("balance", "how much do i have")),
    ("spending_summary", ("spent", "spending")),
    ("credit_passport_issue", ("issue my credit passport", "make my credit passport", "create my credit passport")),
    ("credit_passport_share", ("share proof", "share my credit passport", "share my passport")),
    ("explain_decision", ("why was", "why did", "why is my payment")),
    ("help", ("what can you do", "help me")),
    ("send_money", ("send", "transfer", "remit", "pay ")),
)  # fmt: skip
DEADLINES = (
    ("before dashain", "2026-10-21"), ("before tihar", "2026-11-08"), ("tomorrow", "2026-10-11"),
    ("today", "2026-10-10"),
)  # fmt: skip


def propose(meaning: str) -> dict[str, object]:
    m = meaning.lower()
    intent = next((i for i, words in INTENT_WORDS if any(w in m for w in words)), "")
    slots: dict[str, object] = {}
    amt = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|lakh|hajar)?", m)
    if amt and not re.search(r"\b\d{6,}\b", amt.group(1).replace(",", "")):
        n = float(amt.group(1).replace(",", ""))
        n *= {"k": 1000, "thousand": 1000, "hajar": 1000, "lakh": 100000}.get(amt.group(2) or "", 1)
        if n >= 1 and intent in ("send_money", "pay_invoice", "compare_routes", "convert_currency"):
            slots["amount"] = int(round(n * 100))
    for word, code in CURRENCIES.items():
        if re.search(rf"\b{word}s?\b", m):
            slots["currency"] = code
            break
    for word, key in RELATIONS:
        if word in m:
            slots["payee_ref"] = key
            break
    for word, iso in DEADLINES:
        if word in m:
            slots["deadline"] = iso
            break
    else:
        d = re.search(r"deadline (\d{1,2}) (oct|nov|dec) (\d{4})", m)
        if d:
            month = {"oct": 10, "nov": 11, "dec": 12}[d.group(2)]
            slots["deadline"] = date(int(d.group(3)), month, int(d.group(1))).isoformat()
    if intent == "send_money" and "tomorrow" in m:
        slots["deadline"] = (TODAY + timedelta(days=1)).isoformat()
    return {"intent": intent, "slots": slots}


def shingles(text: str) -> set[str]:
    t = re.sub(r"\s+", " ", text.lower()).strip()
    return {t[i : i + 3] for i in range(max(1, len(t) - 2))}


def held_shingles() -> list[set[str]]:
    out = []
    for name in ("test.jsonl", "candidates.jsonl", "synthetic.jsonl", "human.jsonl"):
        f = HERE / name
        if f.exists():
            out += [shingles(json.loads(x)["text"]) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    return out


def import_sheet(path: Path) -> tuple[list[dict[str, object]], list[str]]:
    held = held_shingles()
    items: list[dict[str, object]] = []
    problems: list[str] = []
    with path.open(encoding="utf-8", newline="") as f:
        for n, row in enumerate(csv.DictReader(f), start=2):
            text = (row.get("message") or "").strip()
            meaning = (row.get("meaning") or "").strip()
            lang = (row.get("language") or "").strip()
            if not text or text.startswith("#"):
                continue
            if lang not in LANGS:
                problems.append(f"row {n}: language must be one of {LANGS}, got {lang!r}")
                continue
            if len(text) > 300 or re.search(r"\d{6,}", re.sub(r"[\s,.-]", "", text)):
                problems.append(f"row {n}: message too long or holds a run of six or more digits")
                continue
            sh = shingles(text)
            if any(len(sh & h) / max(1, len(sh | h)) >= 0.6 for h in held):
                problems.append(f"row {n}: near copy of an existing evaluation item")
                continue
            held.append(sh)
            prop = propose(meaning)
            items.append(
                {
                    "id": f"h{len(items) + 1:03d}", "text": text, "lang_style": lang, "persona": "priya",
                    "intent": prop["intent"], "slots": prop["slots"], "meaning": meaning,
                    "human_written": True, "reviewed": False, "proposed": prop, "source": "human_sheet",
                }
            )  # fmt: skip
    return items, problems


def accept() -> int:
    src = HERE / "human_review.jsonl"
    rows = [json.loads(x) for x in src.read_text(encoding="utf-8").splitlines() if x.strip()]
    ok = [r for r in rows if r.get("reviewed") is True and r.get("intent")]
    out = HERE / "human.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for r in ok:
            r = {k: v for k, v in r.items() if k not in ("proposed", "meaning")}
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(ok)} of {len(rows)} reviewed items written to {out.name}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "import":
        items, problems = import_sheet(Path(argv[1]))
        for p in problems:
            print("SKIPPED:", p)
        with (HERE / "human_review.jsonl").open("w", encoding="utf-8") as f:
            for it in items:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
        empty = sum(1 for it in items if not it["intent"])
        print(f"{len(items)} items for review in human_review.jsonl ({empty} with no proposed intent)")
        return 1 if problems else 0
    if argv[:1] == ["accept"]:
        return accept()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
