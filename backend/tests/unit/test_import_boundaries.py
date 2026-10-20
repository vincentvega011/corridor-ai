"""Structural guarantees checked on the source tree (INV-2, INV-4)."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "corridor"

POLICY_ALLOWED_TOP = {
    "__future__", "collections", "dataclasses", "datetime", "decimal", "enum", "pathlib", "typing",
    "pydantic", "yaml",
}  # fmt: skip
POLICY_ALLOWED_PREFIXES = ("corridor.contracts", "corridor.policy", "corridor.risk")  # risk is pure
# the engine and the rules must not even import the loader's I/O helpers
PURE_FILES = ("rules.py", "engine.py", "state.py")
FORBIDDEN_CALLS = {"now", "utcnow", "today", "open", "read_text", "read_bytes", "random", "urlopen"}


def imports_of(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_policy_imports_only_the_allowlist() -> None:
    bad = []
    for path in sorted((SRC / "policy").glob("*.py")):
        for name in imports_of(path):
            if name.startswith(POLICY_ALLOWED_PREFIXES):
                continue
            if name.split(".")[0] not in POLICY_ALLOWED_TOP:
                bad.append(f"{path.name}: {name}")
    assert not bad, f"policy may not import: {bad}"


def test_policy_has_no_llm_network_database_or_clock() -> None:
    for name in ("llm", "sandbox", "agent", "executor", "ledger", "confirm", "httpx", "sqlalchemy"):
        for path in (SRC / "policy").glob("*.py"):
            assert not any(
                i == name or i.endswith(f".{name}") or i.startswith(f"corridor.{name}")
                for i in imports_of(path)
            ), f"{path.name} imports {name}"


def test_rules_and_engine_make_no_clock_file_or_random_calls() -> None:
    offenders = []
    for fname in PURE_FILES:
        tree = ast.parse((SRC / "policy" / fname).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                called = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                if called in FORBIDDEN_CALLS:
                    offenders.append(f"{fname}:{node.lineno} {called}")
    assert not offenders, offenders


ALLOWED_CONFIRMED = {SRC / "contracts" / "provenance.py", SRC / "confirm" / "upgrade.py"}


def test_user_confirmed_is_created_only_by_corridor_confirm() -> None:
    """No other module may mention the USER_CONFIRMED member or its string value."""
    hits = []
    for path in sorted(SRC.rglob("*.py")):
        if path in ALLOWED_CONFIRMED:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and node.attr == "USER_CONFIRMED":
                hits.append(f"{path.relative_to(SRC)}:{node.lineno}")
            if isinstance(node, ast.Constant) and node.value == "user_confirmed":
                hits.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert not hits, f"USER_CONFIRMED outside corridor.confirm: {hits}"


def test_no_model_construct_on_trusted_types() -> None:
    """``model_construct`` skips validation (and forbidden provenance checks): never used in src."""
    hits = [
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if "model_construct" in p.read_text(encoding="utf-8")
        and p != SRC / "contracts" / "provenance.py"
    ]
    assert not hits, hits


def test_risk_is_pure_like_policy() -> None:
    """The deterministic risk score is called by a policy rule, so it follows the same boundary."""
    for path in sorted((SRC / "risk").glob("*.py")):
        for name in imports_of(path):
            if name.startswith(("corridor.contracts", "corridor.policy", "corridor.risk")):
                continue
            assert name.split(".")[0] in POLICY_ALLOWED_TOP | {"zoneinfo"}, f"{path.name}: {name}"
            assert not any(
                name == f or name.startswith(f"corridor.{f}")
                for f in ("llm", "sandbox", "agent", "executor", "ledger", "confirm")
            ), f"{path.name} imports {name}"
