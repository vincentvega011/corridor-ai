"""NLU evaluation runner (docs/spec/modules.md 5.3 and 8).

    cd backend && uv run python ../eval/nlu/run.py [--file candidates|test|synthetic|human] [--limit N] [--no-cascade]

Metrics: intent accuracy, slot precision/recall/F1 (micro, over (slot, value) pairs), cascade rate,
provenance correctness, p50/p95 latency, each broken down by language style. Ground truth is the
gold labels in the jsonl file; no LLM judge. If any evaluated line is not human approved the report
is stamped DRAFT LABELS and must not be quoted as a result.

Slot comparison forms: amount (integer minor units), currency (code), payee_ref (relation or
lower cased text), deadline (ISO date), recurrence (frequency), condition (reserve word).
Provenance correctness: "typed precision" is the share of USER_TYPED predictions that match a gold
slot (a typed slot with no support in the gold labels is a provenance error); "typed recall" is the
share of correctly predicted gold slots that are USER_TYPED; MODEL_INFERRED slots are counted.
"""

import argparse
import asyncio
import json
import statistics
import sys
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from corridor.contracts import Provenance
from corridor.nlu.parser import ParseResult, parse

HERE = Path(__file__).resolve().parent
REPORTS = HERE.parent / "reports"
TODAY = date(2026, 10, 10)
COMPARED = ("amount", "currency", "payee_ref", "deadline", "recurrence", "condition")


def norm_slot(name: str, value: Any) -> Any:
    """Reduce a predicted slot value to the form used in the gold labels."""
    if name == "payee_ref" and isinstance(value, dict):
        return (value.get("relation") or str(value.get("text", ""))).lower()
    if name == "deadline" and isinstance(value, dict):
        return value.get("date")
    if name == "recurrence" and isinstance(value, dict):
        return value.get("freq")
    if name == "condition" and isinstance(value, dict):
        return value.get("reserve")
    if isinstance(value, str):
        return value.lower() if name == "payee_ref" else value
    return value


def predicted(res: ParseResult) -> dict[str, tuple[Any, Provenance]]:
    return {
        k.value: (norm_slot(k.value, v.value), v.provenance)
        for k, v in res.frame.slots.items()
        if k.value in COMPARED
    }


def pct(n: float, d: float) -> float:
    return round(100 * n / d, 1) if d else 0.0


def quantile(xs: list[int], q: float) -> int:
    if not xs:
        return 0
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


async def evaluate(rows: list[dict[str, Any]], cascade: bool) -> list[dict[str, Any]]:
    results = []
    for row in rows:
        res = await parse(row["text"], TODAY)
        pred = predicted(res)
        gold = {k: v for k, v in row["slots"].items() if k in COMPARED}
        tp = sum(1 for k, v in gold.items() if k in pred and pred[k][0] == v)
        results.append(
            {
                "id": row["id"], "style": row["lang_style"], "gold_intent": row["intent"],
                "pred_intent": res.frame.intent.value, "tp": tp, "n_gold": len(gold), "n_pred": len(pred),
                "typed_pred": sum(1 for k, (v, p) in pred.items() if p is Provenance.USER_TYPED),
                "typed_pred_correct": sum(1 for k, (v, p) in pred.items() if p is Provenance.USER_TYPED and gold.get(k) == v),
                "typed_gold_hits": sum(1 for k, v in gold.items() if k in pred and pred[k][0] == v and pred[k][1] is Provenance.USER_TYPED),
                "inferred": sum(1 for k, (v, p) in pred.items() if p is Provenance.MODEL_INFERRED),
                "cascaded": res.cascade.cascaded, "used": res.cascade.used,
                "confidence_bps": res.frame.confidence_bps, "latency_ms": res.latency_ms,
                "reasons": list(res.cascade.reasons), "text": row["text"],
                "gold_slots": gold, "pred_slots": {k: v[0] for k, v in pred.items()},
            }
        )  # fmt: skip
    return results


def summarise(results: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in results:
        groups["all"].append(r)
        groups[r["style"]].append(r)
    out = {}
    for name, rs in groups.items():
        tp = sum(r["tp"] for r in rs)
        n_gold = sum(r["n_gold"] for r in rs)
        n_pred = sum(r["n_pred"] for r in rs)
        prec, rec = pct(tp, n_pred), pct(tp, n_gold)
        f1 = round(2 * prec * rec / (prec + rec), 1) if prec + rec else 0.0
        lat = [r["latency_ms"] for r in rs]
        out[name] = {
            "n": len(rs),
            "intent_accuracy": pct(sum(r["gold_intent"] == r["pred_intent"] for r in rs), len(rs)),
            "slot_precision": prec, "slot_recall": rec, "slot_f1": f1,
            "cascade_rate": pct(sum(r["cascaded"] for r in rs), len(rs)),
            "typed_precision": pct(sum(r["typed_pred_correct"] for r in rs), sum(r["typed_pred"] for r in rs)),
            "typed_recall": pct(sum(r["typed_gold_hits"] for r in rs), tp),
            "model_inferred_slots": sum(r["inferred"] for r in rs),
            "latency_p50_ms": int(statistics.median(lat)) if lat else 0, "latency_p95_ms": quantile(lat, 0.95),
        }  # fmt: skip
    return out


def render(
    summary: dict[str, Any],
    draft: bool,
    file: str,
    results: list[dict[str, Any]],
    synthetic: bool = False,
) -> str:
    lines = [f"# NLU evaluation: {file}", ""]
    if synthetic:
        lines += [
            "> **SYNTHETIC ITEMS.** Template made, labels known by construction, never human written. Report these numbers",
            "> separately from the human written set and never as a headline NLU result.",
            "",
        ]
    if draft:
        lines += [
            "> **DRAFT LABELS.** These labels have not been reviewed by a human. Do not quote these numbers as results.",
            "",
        ]
    head = "| style | n | intent acc % | slot P % | slot R % | slot F1 % | cascade % | typed prec % | typed rec % | inferred | p50 ms | p95 ms |"
    lines += [head, "|" + "---|" * 12]
    for name in ("all", "en", "ne", "ne_latn", "mixed"):
        s = summary.get(name)
        if s:
            lines.append(
                f"| {name} | {s['n']} | {s['intent_accuracy']} | {s['slot_precision']} | {s['slot_recall']} | {s['slot_f1']} | {s['cascade_rate']} | {s['typed_precision']} | {s['typed_recall']} | {s['model_inferred_slots']} | {s['latency_p50_ms']} | {s['latency_p95_ms']} |"
            )
    wrong = [r for r in results if r["gold_intent"] != r["pred_intent"] or r["tp"] < r["n_gold"]]
    lines += ["", f"## Errors ({len(wrong)} of {len(results)} items differ from the labels)", ""]
    for r in wrong[:60]:
        lines.append(
            f"- `{r['id']}` [{r['style']}] {r['text']!r}: intent {r['gold_intent']} -> {r['pred_intent']}; gold {r['gold_slots']} pred {r['pred_slots']}; cascade={r['used'] if r['cascaded'] else 'no'}"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="candidates", choices=["candidates", "test", "synthetic", "human"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument(
        "--no-cascade", action="store_true", help="documented flag, cascade stays on (reserved)"
    )
    args = ap.parse_args()
    path = HERE / f"{args.file}.jsonl"
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    if args.limit:
        rows = rows[: args.limit]
    synthetic = any(r.get("synthetic") for r in rows)
    draft = not synthetic and any(
        r.get("status", "approved") not in ("approved", "moved") and not r.get("reviewed")
        for r in rows
    )
    results = asyncio.run(evaluate(rows, not args.no_cascade))
    summary = summarise(results)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = REPORTS / f"nlu_{args.file}_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(
            {"draft_labels": draft, "synthetic": synthetic, "summary": summary, "items": results},
            ensure_ascii=False,
            indent=1,
        )
    )
    md = render(summary, draft, path.name, results, synthetic)
    (out_dir / "report.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"report: {out_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
