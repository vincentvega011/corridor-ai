"""Lint and count the task suite.

Run: ``cd backend && uv run python ../eval/tasks_stats.py``.

Checks: unique ids, a known scenario, known assertion kinds, fixtures that exist, no banned dashes,
and prints the count per Topic C area (``scenario``), persona, language style and dialogue feature
(``features``).
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
AREAS = ("remittance", "tuition", "credit", "multi_currency")
TASK_KINDS = frozenset(
    {
        "balance_delta",
        "transfers_created",
        "invoice_status",
        "invoice_paid_minor",
        "contribution_requests",
        "presentation_discloses",
        "presentation_verified",
        "ledger_event",
        "event",
        "no_event",
        "reply_matches",
        "transfers_gt",
    }
)
BANNED_DASHES = (chr(0x2014), chr(0x2013))


def load_task_dicts() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in sorted((HERE / "tasks").glob("*.yaml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or []
        out += data if isinstance(data, list) else [data]
    return out


def lint(tasks: list[dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    ids = [t["id"] for t in tasks]
    problems += [f"duplicate id {i}" for i, n in Counter(ids).items() if n > 1]
    for t in tasks:
        if t.get("scenario") not in AREAS:
            problems.append(f"{t['id']}: unknown scenario {t.get('scenario')}")
        if "features" not in t:
            problems.append(f"{t['id']}: no features list (use [])")
        for a in [*t.get("expected_state", []), *t.get("forbidden_actions", [])]:
            if a.get("kind") not in TASK_KINDS:
                problems.append(f"{t['id']}: unknown assertion kind {a.get('kind')}")
        for turn in t["turns"]:
            if isinstance(turn, dict):
                for p in turn.get("attach", []):
                    if not (ROOT / "data" / "fixtures" / p).is_file():
                        problems.append(f"{t['id']}: fixture not found {p}")
        text = yaml.safe_dump(t, allow_unicode=True)
        if any(d in text for d in BANNED_DASHES):
            problems.append(f"{t['id']}: banned dash character")
    return problems


def main() -> int:
    tasks = load_task_dicts()
    problems = lint(tasks)
    for p in problems:
        print("PROBLEM:", p)
    print(f"{len(tasks)} tasks")
    print("area:", dict(Counter(t["scenario"] for t in tasks)))
    print("persona:", dict(Counter(t["persona"] for t in tasks)))
    print("language:", dict(Counter(t["language"] for t in tasks)))
    print("feature:", dict(Counter(f for t in tasks for f in t.get("features", []))))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
