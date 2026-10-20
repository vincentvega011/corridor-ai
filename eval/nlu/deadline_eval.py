"""Deadline-only accuracy without any model (the slot builder with an empty model output).

    cd backend && uv run python ../eval/nlu/deadline_eval.py [--file test|deadline_holdout]

The deadline slot is built by code from the user's own words (``normalise`` and ``build_slots``); the model
output is only a hint, so the model is replaced by an empty ParserOutput. Gold NEEDS_CLARIFICATION is right only when no deadline is built. Prints accuracy and each miss.
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from run import norm_slot  # noqa: E402

from corridor.contracts.intent import IntentType, LanguageTag  # noqa: E402
from corridor.contracts.llm_outputs import ParserOutput  # noqa: E402
from corridor.nlu.lang_pack import load_packs  # noqa: E402
from corridor.nlu.normalise import normalise  # noqa: E402
from corridor.nlu.slots import build_slots  # noqa: E402

TODAY = date(2026, 10, 10)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="test")
    a = ap.parse_args()
    packs = load_packs()
    rows = [
        json.loads(x)
        for x in (HERE / f"{a.file}.jsonl").read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]
    ok = n = 0
    misses = []
    for r in rows:
        gold = r["slots"].get("deadline")
        if gold is None:
            continue
        n += 1
        out = ParserOutput(
            language=LanguageTag.EN, intent=IntentType.SEND_MONEY, confidence_bps=9000
        )
        built = build_slots(out, normalise(r["text"], TODAY, packs), packs)
        v = next(
            (
                norm_slot("deadline", s.value)
                for k, s in built.slots.items()
                if k.value == "deadline"
            ),
            None,
        )
        if v == gold or (gold == "NEEDS_CLARIFICATION" and v is None):
            ok += 1
        else:
            misses.append((r["id"], r["text"], gold, v, built.ambiguous.get("deadline")))
    print(f"{a.file}: deadline accuracy {ok}/{n} = {100 * ok / n:.1f}%")
    for m in misses:
        print("  MISS", m)


main()
