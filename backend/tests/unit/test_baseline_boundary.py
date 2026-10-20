"""Only the compare endpoint (and the eval runner) may import the baseline (ADR 0010)."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "corridor"
ALLOWED = {SRC / "api" / "compare.py"}
SAFETY_PACKAGES = (
    "policy",
    "taint",
    "confirm",
    "executor",
    "ledger",
    "grounding",
    "reader",
    "agent",
    "contracts",
)


def _imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out |= {f"{node.module}.{a.name}" for a in node.names}
    return out


def test_no_full_mode_module_imports_the_baseline() -> None:
    offenders = []
    for f in SRC.rglob("*.py"):
        if (SRC / "baseline") in f.parents or f in ALLOWED:
            continue
        if any(i == "corridor.baseline" or i.startswith("corridor.baseline.") for i in _imports(f)):
            offenders.append(str(f.relative_to(SRC)))
    assert not offenders, offenders


def test_the_baseline_never_imports_the_executor_policy_or_ledger() -> None:
    """The naive agent must not borrow a defence (or the only writer) from the full system."""
    banned = (
        "corridor.policy",
        "corridor.executor",
        "corridor.confirm",
        "corridor.ledger",
        "corridor.taint",
        "corridor.grounding",
        "corridor.agent.graph",
        "corridor.agent.service",
    )
    bad = []
    for f in (SRC / "baseline").rglob("*.py"):
        for i in _imports(f):
            if any(i == b or i.startswith(b + ".") for b in banned):
                bad.append((f.name, i))
    assert not bad, bad


def test_chat_and_the_service_do_not_reference_the_baseline() -> None:
    for name in ("api/app.py", "api/main.py", "agent/service.py", "agent/graph.py"):
        text = (SRC / name).read_text(encoding="utf-8")
        assert "corridor.baseline" not in text.replace("corridor.api.compare", ""), name


def test_the_compare_module_is_only_wired_in_dev_and_demo() -> None:
    text = (SRC / "api" / "main.py").read_text(encoding="utf-8")
    assert "AppEnv.DEV, AppEnv.DEMO" in text and "build_compare" in text


def test_baseline_alias_resolution_accepts_the_last_four_when_unambiguous() -> None:
    """P2-6: a validation bug (the model quoted "2208" and the tool said unknown) must not stop the baseline."""
    from corridor.baseline.tools import AccountAliases

    a = AccountAliases()
    a.alias_text("Account number: 9271 0033 2208")
    full = "927100332208"
    assert a.resolve("ACCT-1") == full and a.resolve("[ACCT-1 ending 2208]") == full
    assert (
        a.resolve("2208") == full
        and a.resolve("ending 2208") == full
        and a.resolve("****2208") == full
    )
    assert a.resolve("9999") is None  # unknown last four
    a.alias_text("Other: 5555 0033 2208")  # two accounts end the same way: ambiguous, so None
    assert a.resolve("2208") is None
