"""No code path from user or model input can set a payee to verified (ADR 0012, INV-1, INV-4)."""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "corridor"
SETS_VERIFIED = re.compile(r"verified_at\s*=\s*(?!None\b)\S")
ALLOWED = {
    "executor/verify.py",  # the officer action, after a CoP match
    "sandbox/seed.py",  # synthetic seed data
}


def test_only_the_officer_action_and_the_seed_ever_set_verified_at() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if SETS_VERIFIED.search(line) and rel not in ALLOWED:
                offenders.append(f"{rel}:{n}: {line.strip()}")
    assert not offenders, offenders


def test_verify_payee_is_not_an_agent_tool_or_action_and_the_agent_never_imports_it() -> None:
    from corridor.contracts.action import ActionType
    from corridor.sandbox.read_tools import READ_TOOLS

    assert "verify_payee" not in {a.value for a in ActionType}
    assert not any("verify" in name for name in READ_TOOLS)
    for path in (SRC / "agent").rglob("*.py"):
        assert "verify_payee" not in path.read_text(encoding="utf-8"), path.name
    assert "verify_payee" not in (SRC / "baseline" / "tools.py").read_text(encoding="utf-8")
