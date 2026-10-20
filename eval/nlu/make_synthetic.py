"""Template based SYNTHETIC NLU items (P3-4). Deterministic, no model, no network.

    python3 eval/nlu/make_synthetic.py            # writes eval/nlu/synthetic.jsonl

Every item carries ``"synthetic": true`` and ``"eval_only": true``: it is an evaluation item, never a training
item. Gold labels are known by construction (the template decides the intent and the slot values), the same
conventions as ``test.jsonl`` (amount in minor units, relation keys, ISO deadlines for "today" = 2026-10-10).
Items too similar to ``test.jsonl`` or ``candidates.jsonl`` are dropped. Human-written items go through
``eval/nlu/import_human.py`` into ``human.jsonl``; the two sets are always reported separately.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = 20261012
DEVA = str.maketrans("0123456789", "०१२३४५६७८९")

REL = {  # relation key: (en, ne_latn, ne dative)
    "mother": ("my mother", "Ama lai", "आमालाई"),
    "father": ("my father", "Buwa lai", "बुवालाई"),
    "elder_brother": ("my elder brother", "dai lai", "दाइलाई"),
    "younger_brother": ("my younger brother", "bhai lai", "भाइलाई"),
    "elder_sister": ("my elder sister", "didi lai", "दिदीलाई"),
    "younger_sister": ("my younger sister", "bahini lai", "बहिनीलाई"),
}
DEADLINES = {  # key: (en, ne_latn, ne, iso)
    "tomorrow": ("by tomorrow", "bholi samma", "भोलि सम्म", "2026-10-11"),
    "dashain": ("before Dashain", "dashain bhanda agadi", "दशैँ भन्दा अगाडि", "2026-10-21"),
    "tihar": ("before Tihar", "tihar bhanda pahile", "तिहार भन्दा अगाडि", "2026-11-08"),
    "oct25": ("by 25 Oct", "25 oct samma", "२५ अक्टोबर सम्म", "2026-10-25"),
    "nov15": ("by 15 Nov", "15 nov samma", "१५ नोभेम्बर सम्म", "2026-11-15"),
}
AMOUNTS_HAJAR = (3, 4, 6, 7, 8, 9, 12, 14, 15, 16, 18, 22, 28, 35, 38, 45, 55, 60, 65, 75, 85, 95)
AMOUNTS_CODE = (("QAR", 250, 25000), ("QAR", 700, 70000), ("AUD", 150, 15000), ("AUD", 900, 90000), ("CNY", 800, 80000), ("CNY", 2500, 250000))
CURRENCY_WORD = {"NPR": ("rupees", "rupiya", "रुपैयाँ"), "QAR": ("riyal", "riyal", "रियाल"), "CNY": ("yuan", "yuan", "युआन")}


def deva(n: int) -> str:
    return str(n).translate(DEVA)


def send_items(rng: random.Random) -> list[dict]:
    out: list[dict] = []
    rels = list(REL)
    # 1. Devanagari: "<amount> हजार <currency?> <payee> पठाउ"
    for i in range(16):
        rel, n = rels[i % len(rels)], rng.choice(AMOUNTS_HAJAR)
        slots: dict = {"amount": n * 100000, "payee_ref": rel}
        text = f"{REL[rel][2]} {deva(n)} हजार पठाउ"
        if i % 3 == 0:
            text = f"{REL[rel][2]} {deva(n)} हजार रुपैयाँ पठाइदेउ"
            slots["currency"] = "NPR"
        if i % 4 == 1:
            dk = rng.choice(["tomorrow", "dashain", "tihar"])
            text = f"{DEADLINES[dk][2]} {text}"
            slots["deadline"] = DEADLINES[dk][3]
        out.append({"text": text, "lang_style": "ne", "persona": "ramesh", "intent": "send_money", "slots": slots})
    # 2. romanised Nepali
    for i in range(20):
        rel, n = rels[(i + 2) % len(rels)], rng.choice(AMOUNTS_HAJAR)
        slots = {"amount": n * 100000, "payee_ref": rel}
        text = f"{REL[rel][1]} {n} hajar pathau"
        if i % 3 == 0:
            text = f"{REL[rel][1]} {n} hajar rupiya pathaideu"
            slots["currency"] = "NPR"
        if i % 4 == 1:
            dk = rng.choice(["tomorrow", "dashain", "tihar", "oct25"])
            text = f"{DEADLINES[dk][1]} {text}"
            slots["deadline"] = DEADLINES[dk][3]
        out.append({"text": text, "lang_style": "ne_latn", "persona": "ramesh", "intent": "send_money", "slots": slots})
    for i in range(6):  # a code amount
        cur, major, minor = AMOUNTS_CODE[i]
        rel = rels[(i + 1) % len(rels)]
        text = f"{REL[rel][1]} {major} {cur} pathau"
        out.append({"text": text, "lang_style": "ne_latn", "persona": "ramesh", "intent": "send_money", "slots": {"amount": minor, "currency": cur, "payee_ref": rel}})
    # 3. English
    for i in range(16):
        rel = rels[(i + 3) % len(rels)]
        cur, major, minor = AMOUNTS_CODE[i % len(AMOUNTS_CODE)]
        text = f"Please send {cur} {major} to {REL[rel][0]}"
        slots = {"amount": minor, "currency": cur, "payee_ref": rel}
        if i % 3 == 2:
            dk = rng.choice(["dashain", "oct25", "nov15"])
            text = f"{text} {DEADLINES[dk][0]}"
            slots["deadline"] = DEADLINES[dk][3]
        out.append({"text": text, "lang_style": "en", "persona": "priya", "intent": "send_money", "slots": slots})
    # 4. mixed
    for i in range(6):
        rel, n = rels[(i + 4) % len(rels)], rng.choice(AMOUNTS_HAJAR)
        text = [f"send {n}k to {REL[rel][1].split()[0]}", f"{REL[rel][1]} {n} hajar transfer gardeu please", f"{REL[rel][2]} send {n} hajar", f"please {REL[rel][1]} {n}k send gardeu", f"pls send {n} hajar to {REL[rel][1].split()[0]}", f"{REL[rel][1]} {n} hajar send gardinu hola"][i]
        out.append({"text": text, "lang_style": "mixed", "persona": "ramesh", "intent": "send_money", "slots": {"amount": n * 100000, "payee_ref": rel}})
    # 5. a missing slot: asking for the amount or the payee is the right behaviour
    for text, ls, slots in (
        ("send money to my younger sister", "en", {"payee_ref": "younger_sister"}),
        ("बुवालाई पैसा पठाउ", "ne", {"payee_ref": "father"}),
        ("bahini lai paisa pathau", "ne_latn", {"payee_ref": "younger_sister"}),
        ("I want to send 12 hajar", "mixed", {"amount": 1200000}),
    ):
        out.append({"text": text, "lang_style": ls, "persona": "priya", "intent": "send_money", "slots": slots})
    return out


def other_items() -> list[dict]:
    rows: list[tuple[str, str, str, dict]] = [
        # pay_invoice
        ("pay my tuition invoice", "en", "pay_invoice", {"payee_ref": "university"}),
        ("please settle the university fee invoice", "en", "pay_invoice", {"payee_ref": "university"}),
        ("pay the university invoice before Dashain", "en", "pay_invoice", {"payee_ref": "university", "deadline": "2026-10-21"}),
        ("pay my tuition invoice by 25 Oct", "en", "pay_invoice", {"deadline": "2026-10-25"}),
        ("विश्वविद्यालयको बिल तिर", "ne", "pay_invoice", {"payee_ref": "university"}),
        ("ट्यूसन फी तिर्नुपर्छ", "ne", "pay_invoice", {}),
        ("university ko invoice tirideu", "ne_latn", "pay_invoice", {"payee_ref": "university"}),
        ("fee tirna parne chha 15 nov samma", "ne_latn", "pay_invoice", {"deadline": "2026-11-15"}),
        ("tuition invoice pay gardinu", "mixed", "pay_invoice", {}),
        ("pay the fee invoice please, university lai", "mixed", "pay_invoice", {"payee_ref": "university"}),
        # check_balance
        ("what is my balance", "en", "check_balance", {}),
        ("show me my QAR balance", "en", "check_balance", {"currency": "QAR"}),
        ("how much do I have in AUD", "en", "check_balance", {"currency": "AUD"}),
        ("मेरो खातामा कति छ", "ne", "check_balance", {}),
        ("खाताको मौज्दात देखाऊ", "ne", "check_balance", {}),
        ("balance kati chha ahile", "ne_latn", "check_balance", {}),
        ("CNY ko balance herna paryo", "ne_latn", "check_balance", {"currency": "CNY"}),
        ("check my balance gardeu", "mixed", "check_balance", {}),
        # compare_routes
        ("cheapest way to send 800 QAR home", "en", "compare_routes", {"amount": 80000, "currency": "QAR"}),
        ("compare routes for AUD 1,200", "en", "compare_routes", {"amount": 120000, "currency": "AUD"}),
        ("which provider is best for 2000 CNY", "en", "compare_routes", {"amount": 200000, "currency": "CNY"}),
        ("१० हजार पठाउन सबैभन्दा सस्तो उपाय", "ne", "compare_routes", {"amount": 1000000}),
        ("कुन तरिका सस्तो छ ५०० रियाल पठाउन", "ne", "compare_routes", {"amount": 50000, "currency": "QAR"}),
        ("sabai bhanda sasto kun route ho 600 QAR ko lagi", "ne_latn", "compare_routes", {"amount": 60000, "currency": "QAR"}),
        ("1000 AUD pathauna kun provider sasto", "ne_latn", "compare_routes", {"amount": 100000, "currency": "AUD"}),
        ("cheapest route kun ho for 500 QAR", "mixed", "compare_routes", {"amount": 50000, "currency": "QAR"}),
        # convert_currency
        ("how much is 300 AUD in NPR", "en", "convert_currency", {"amount": 30000, "currency": "AUD"}),
        ("convert QAR 500 to NPR", "en", "convert_currency", {"amount": 50000, "currency": "QAR"}),
        ("५०० रियाल नेपाली रुपैयाँमा कति हुन्छ", "ne", "convert_currency", {"amount": 50000, "currency": "QAR"}),
        ("1000 CNY ko NPR kati huncha", "ne_latn", "convert_currency", {"amount": 100000, "currency": "CNY"}),
        # schedule_transfer / cancel_schedule
        ("send 20 hajar to Ama every month", "mixed", "schedule_transfer", {"amount": 2000000, "payee_ref": "mother", "recurrence": "monthly"}),
        ("every month Ama lai 15 hajar pathau", "ne_latn", "schedule_transfer", {"amount": 1500000, "payee_ref": "mother", "recurrence": "monthly"}),
        ("हरेक महिना बुवालाई १० हजार पठाउ", "ne", "schedule_transfer", {"amount": 1000000, "payee_ref": "father", "recurrence": "monthly"}),
        ("set up my rent to be paid every month", "en", "schedule_transfer", {"recurrence": "monthly"}),
        ("cancel my monthly transfer to Ama", "en", "cancel_schedule", {"payee_ref": "mother"}),
        ("Ama ko mahina ko pathaune band gardeu", "ne_latn", "cancel_schedule", {"payee_ref": "mother"}),
        # add_payee
        ("add a new payee", "en", "add_payee", {}),
        ("save my new landlord as a payee", "en", "add_payee", {}),
        ("नयाँ प्राप्तकर्ता थप", "ne", "add_payee", {}),
        ("naya payee save gardeu", "ne_latn", "add_payee", {}),
        # help / explain / summary / passport / budget / other
        ("what can you do", "en", "help", {}),
        ("तिमीले के गर्न सक्छौ", "ne", "help", {}),
        ("kasari use garne yo app", "ne_latn", "help", {}),
        ("why was my payment refused", "en", "explain_decision", {}),
        ("किन रोकियो मेरो भुक्तानी", "ne", "explain_decision", {}),
        ("kina payment roki", "ne_latn", "explain_decision", {}),
        ("how much did I spend this month", "en", "spending_summary", {}),
        ("यो महिना कति खर्च भयो", "ne", "spending_summary", {}),
        ("yo mahina ko kharcha dekhau", "ne_latn", "spending_summary", {}),
        ("make my credit passport", "en", "credit_passport_issue", {}),
        ("क्रेडिट पासपोर्ट बनाऊ", "ne", "credit_passport_issue", {}),
        ("share proof that I pay rent on time", "en", "credit_passport_share", {}),
        ("rent time ma tirne proof share gardeu", "ne_latn", "credit_passport_share", {}),
        ("set a food budget of 400 AUD a month", "en", "set_budget", {"amount": 40000, "currency": "AUD"}),
        ("what is the weather in Sydney", "en", "other", {}),
        ("how much money is in my savings account", "en", "check_balance", {}),
        ("show my NPR balance please", "en", "check_balance", {"currency": "NPR"}),
        ("pay the invoice from Harbourview", "en", "pay_invoice", {}),
        ("which route arrives before Tihar for 700 QAR", "en", "compare_routes", {"amount": 70000, "currency": "QAR", "deadline": "2026-11-08"}),
        ("convert AUD 1,000 to NPR", "en", "convert_currency", {"amount": 100000, "currency": "AUD"}),
        ("help me understand this screen", "en", "help", {}),
        ("मेरो खर्चको सारांश देखाऊ", "ne", "spending_summary", {}),
        ("आज कति पैसा बाँकी छ", "ne", "check_balance", {}),
        ("मेरो क्रेडिट पासपोर्ट सेयर गर", "ne", "credit_passport_share", {}),
        ("kharcha ko summary dekhau", "ne_latn", "spending_summary", {}),
        ("credit passport banaideu", "ne_latn", "credit_passport_issue", {}),
        ("मलाई एउटा कविता सुनाऊ", "ne", "other", {}),
    ]
    return [{"text": t, "lang_style": ls, "persona": "priya", "intent": i, "slots": s} for t, ls, i, s in rows]


def shingles(text: str) -> set[str]:
    t = re.sub(r"\s+", " ", text.lower()).strip()
    return {t[i : i + 3] for i in range(max(1, len(t) - 2))}


def too_similar(text: str, held: list[set[str]]) -> bool:
    sh = shingles(text)
    return any(len(sh & h) / max(1, len(sh | h)) >= 0.6 for h in held)


def main() -> int:
    rng = random.Random(SEED)
    held = [
        shingles(json.loads(x)["text"])
        for name in ("test.jsonl", "candidates.jsonl")
        for x in (HERE / name).read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]
    items: list[dict] = []
    seen: set[str] = set()
    for row in [*send_items(rng), *other_items()]:
        key = row["text"].lower()
        if key in seen or too_similar(row["text"], held):
            continue
        seen.add(key)
        items.append(row)
    with (HERE / "synthetic.jsonl").open("w", encoding="utf-8") as f:
        for n, row in enumerate(items, 1):
            row = {"id": f"s{n:03d}", **row, "synthetic": True, "eval_only": True, "reviewed": False, "source": "template_p34"}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{len(items)} synthetic items written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
