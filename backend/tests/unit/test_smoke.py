"""Smoke test: the package imports and settings load with validated enums."""

import importlib
from pathlib import Path

import pytest
from pydantic import ValidationError

import corridor
from corridor.config import AppEnv, CorridorMode, Settings, get_settings
from corridor.logging import configure_logging, get_logger, new_trace_id

REPO_ROOT = Path(__file__).resolve().parents[3]

MODULES = [
    "api",
    "agent",
    "nlu",
    "reader",
    "taint",
    "policy",
    "confirm",
    "executor",
    "ledger",
    "optimizer",
    "risk",
    "passport",
    "grounding",
    "sandbox",
    "llm",
    "contracts",
]


def test_package_version() -> None:
    assert corridor.__version__


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name: str) -> None:
    mod = importlib.import_module(f"corridor.{name}")
    assert mod.__doc__


def test_config_defaults() -> None:
    s = get_settings()
    assert s.app_env in AppEnv
    assert s.corridor_mode in CorridorMode


def test_config_rejects_invalid_enums() -> None:
    with pytest.raises(ValidationError):
        Settings(app_env="staging")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Settings(corridor_mode="yolo")  # type: ignore[arg-type]


def test_env_example_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)  # the Makefile sets APP_ENV for the test run
    s = Settings(_env_file=REPO_ROOT / ".env.example")  # type: ignore[call-arg]
    assert s.app_env is AppEnv.DEV
    assert s.parser_model == "default_model"
    assert s.planner_base_url == ""
    assert s.llm_record is False


def test_logging_carries_trace_id(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging()
    tid = new_trace_id()
    get_logger("smoke").info("hello")
    assert tid in capsys.readouterr().out
