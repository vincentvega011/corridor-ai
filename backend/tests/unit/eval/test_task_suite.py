"""The task suite is well formed and meets the size and coverage floor of docs/spec/evaluation-spec.md 8.1."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "eval"))

import tasks_stats  # noqa: E402


def test_task_suite_is_valid_and_covers_every_topic_c_area() -> None:
    tasks = tasks_stats.load_task_dicts()
    assert tasks_stats.lint(tasks) == []
    assert len(tasks) >= 60
    areas = Counter(t["scenario"] for t in tasks)
    assert all(areas[a] >= 5 for a in tasks_stats.AREAS)
    assert {"en", "ne", "ne_latn"} <= {t["language"] for t in tasks}
    features = {f for t in tasks for f in t["features"]}
    assert {
        "clarification",
        "correction",
        "cancellation",
        "grant_invalidation",
        "fallback",
        "festival",
    } <= features


def test_partial_results_are_ignored_by_git_and_never_the_published_report() -> None:
    import subprocess

    assert "eval/reports/partial.json" in (ROOT / ".gitignore").read_text()
    tracked = subprocess.run(
        ["git", "ls-files", "eval/reports/partial.json"], cwd=ROOT, capture_output=True, text=True
    ).stdout
    assert tracked.strip() == ""
    # the console and the report writer publish only through the latest.json link
    officer = (ROOT / "backend/src/corridor/api/officer.py").read_text()
    assert "partial" not in officer
