"""Application settings loaded from environment variables (and an optional .env file).

Secrets (API keys, signing key paths) are only ever read here and must never be
logged or placed in prompts (INV-12).
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
"""Repository root (config.py lives in backend/src/corridor/). The .env file lives here."""


class AppEnv(StrEnum):
    """Deployment environment. Ablation flags are allowed only in TEST and EVAL (ADR 0010)."""

    DEV = "dev"
    TEST = "test"
    EVAL = "eval"
    DEMO = "demo"
    PRODUCTION = "production"
    """The default: no dev route, no sandbox authenticator, no demo clock, no officer routes."""


class CorridorMode(StrEnum):
    """Runtime mode: the full defended agent or the naive baseline used for comparison."""

    FULL = "full"
    BASELINE = "baseline"


class Settings(BaseSettings):
    """All environment variables from .env.example."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_env: AppEnv = (
        AppEnv.PRODUCTION
    )  # fail safe: dev, demo, test and eval are always set explicitly
    corridor_mode: CorridorMode = CorridorMode.FULL
    database_url: str = "postgresql+psycopg://corridor:corridor@localhost:5432/corridor"
    baseline_database_url: str = (
        ""  # default: DATABASE_URL with the database name corridor_baseline
    )

    corridor_pg_port: int = 5432
    frontend_origin: str = "http://localhost:3000"  # the only browser origin allowed by CORS
    trusted_proxy_header: str = (
        ""  # e.g. X-Forwarded-For when behind a proxy we run; empty: remote address
    )
    demo_clock: str = (
        ""  # "on" fixes the sandbox date to the documented demo date (dev and demo only)
    )

    tokenharbor_base_url: str = "https://tokenharbor.ai/v1"
    tokenharbor_api_key: SecretStr = SecretStr("")

    # All cloud roles (planner, composer, parser_fallback) share this endpoint and key.
    # PLANNER_BASE_URL must be a full URL, for example
    # https://<WorkspaceId>.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1
    planner_base_url: str = ""
    planner_api_key: SecretStr = SecretStr("")
    planner_model: str = ""
    composer_model: str = ""
    parser_fallback_model: str = ""

    parser_base_url: str = "http://localhost:8081/v1"
    parser_model: str = "corridor-parser"

    reader_base_url: str = "http://localhost:11434/v1"
    reader_model: str = ""

    local_planner_base_url: str = "http://localhost:8082/v1"
    local_planner_model: str = ""

    # Ablation flags (eval only, ADR 0010). Each removes exactly one defence; refused unless APP_ENV is
    # test or eval, and refused unless the database is an evaluation database.
    abl_no_quarantine: bool = False
    abl_no_taint: bool = False
    abl_no_policy: bool = False
    abl_no_grants: bool = False
    abl_no_grounding: bool = False
    abl_no_risk: bool = False
    abl_no_cooloff: bool = False

    llm_record: bool = False
    llm_record_roles: str = (
        ""  # comma list of roles to call live and record while the rest replays (e2e recording)
    )
    llm_replay: bool = False

    ledger_signing_key_path: str = "./secrets/issuer_ed25519.pem"

    @model_validator(mode="after")
    def _database_url_follows_port(self) -> Settings:
        """When DATABASE_URL is not set, point at the compose Postgres on CORRIDOR_PG_PORT.

        Lets a second copy of the repo run beside the first with only a different port.
        """
        if "database_url" not in self.model_fields_set:
            self.database_url = (
                f"postgresql+psycopg://corridor:corridor@localhost:{self.corridor_pg_port}/corridor"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the cached process-wide settings."""
    return Settings()
