"""Count the red-team suite by official scoring category.

Run: ``cd backend && uv run python ../eval/redteam_stats.py``. The four scoring categories of the competition
(induced transfers, data extraction, privilege escalation, prompt injection) are mapped from the nine suite
categories; `hallucination` is the grounding bait and is reported on its own.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.cases import Case, load_cases  # noqa: E402

SCORING = {
    "induced_transfer_social_engineering": "induced transfers",
    "confirmation_swap": "induced transfers",
    "replay_duplicate": "induced transfers",
    "data_extraction": "data extraction",
    "privilege_escalation": "privilege escalation",
    "direct_injection": "prompt injection",
    "indirect_injection_document": "prompt injection",
    "indirect_injection_image": "prompt injection",
    "hallucination": "grounding bait",
}


def summary(cases: list[Case]) -> dict[str, object]:
    attacks = [c for c in cases if not c.control]
    return {
        "total": len(cases),
        "attacks": len(attacks),
        "controls": len(cases) - len(attacks),
        "scoring_category": dict(Counter(SCORING[c.category] for c in attacks)),
        "suite_category": dict(Counter(c.category for c in attacks)),
        "adaptive": sum("adaptive" in c.tags for c in attacks),
        "language": dict(Counter(c.language for c in attacks)),
    }


def main() -> int:
    for key, value in summary(load_cases()).items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
