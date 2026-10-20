"""Ship gate for the fine-tuned parser: the LOCAL parser alone on eval/nlu/test.jsonl (no cloud fallback).

    cd backend && uv run python ../eval/nlu/ship_gate.py --label current|finetuned [--zero-shot]

One model server (port 8081) at a time. Reports JSON/schema validity, intent accuracy, per-slot-field accuracy,
overall slot F1 and latency, from the same code path production uses (``build_messages`` then ``build_slots``).
``--zero-shot`` drops the few-shot turns (the shape the adapter was trained on). Output: logs/shipgate_<label>.json.
The fine-tuned parser replaces the current one ONLY if it is more accurate AND JSON validity is 100%.
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from run import COMPARED, norm_slot  # noqa: E402

from corridor.contracts.llm_outputs import ParserOutput  # noqa: E402
from corridor.llm import ModelRole, get_gateway  # noqa: E402
from corridor.nlu.lang_pack import load_packs  # noqa: E402
from corridor.nlu.normalise import normalise  # noqa: E402
from corridor.nlu.parser import build_messages, user_message  # noqa: E402
from corridor.nlu.slots import build_slots  # noqa: E402

TODAY = date(2026, 10, 10)


async def main_async(label: str, zero_shot: bool) -> dict[str, object]:
    packs, gw = load_packs(), get_gateway()
    rows = [
        json.loads(x)
        for x in (HERE / "test.jsonl").read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]
    system = (
        (HERE.parents[1] / "backend/src/corridor/nlu/prompts/parser_v1.txt")
        .read_text(encoding="utf-8")
        .strip()
    )
    valid = intent_ok = tp = n_pred = n_gold = 0
    per = {k: [0, 0] for k in COMPARED}  # [correct, gold]
    lat: list[float] = []
    fails: list[str] = []
    for row in rows:
        norm = normalise(row["text"], TODAY, packs)
        msgs = (
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user_message(row["text"], norm)},
            ]
            if zero_shot
            else build_messages(row["text"], norm, packs)
        )
        t = time.perf_counter()
        try:
            res = await gw.complete(ModelRole.PARSER, msgs, schema=ParserOutput)
            out = res.parsed if isinstance(res.parsed, ParserOutput) else None
        except (
            Exception
        ) as exc:  # an invalid output or a server error is an invalid result, never a pass
            out = None
            fails.append(f"{row['id']}:{type(exc).__name__}")
        lat.append((time.perf_counter() - t) * 1000)
        gold = {k: v for k, v in row["slots"].items() if k in COMPARED}
        for k in gold:
            per[k][1] += 1
        n_gold += len(gold)
        if out is None:
            continue
        valid += 1
        built = build_slots(out, norm, packs)
        pred = {
            k.value: norm_slot(k.value, v.value)
            for k, v in built.slots.items()
            if k.value in COMPARED
        }
        intent_ok += out.intent.value == row["intent"]
        n_pred += len(pred)
        for k, v in gold.items():
            if k in pred and pred[k] == v:
                per[k][0] += 1
                tp += 1
    n = len(rows)
    p, r = (tp / n_pred if n_pred else 0.0), (tp / n_gold if n_gold else 0.0)
    out_d: dict[str, object] = {
        "label": label,
        "zero_shot": zero_shot,
        "n": n,
        "json_valid": valid,
        "json_validity_pct": round(100 * valid / n, 1),
        "intent_accuracy_pct": round(100 * intent_ok / n, 1),
        "slot_f1_pct": round(100 * (2 * p * r / (p + r) if p + r else 0.0), 1),
        "slot_accuracy_by_field_pct": {
            k: (round(100 * c / g, 1) if g else None) for k, (c, g) in per.items()
        },
        "latency_p50_ms": int(statistics.median(lat)),
        "latency_p95_ms": int(sorted(lat)[int(0.95 * len(lat)) - 1]),
        "failures": fails[:10],
    }
    return out_d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--zero-shot", action="store_true")
    a = ap.parse_args()
    res = asyncio.run(main_async(a.label, a.zero_shot))
    out = HERE.parents[1] / "logs" / f"shipgate_{a.label}{'_zeroshot' if a.zero_shot else ''}.json"
    out.write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps(res))


if __name__ == "__main__":
    main()
