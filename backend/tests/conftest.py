"""Shared fixtures."""

from datetime import UTC, datetime

import pytest


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


import os  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from pathlib import Path  # noqa: E402

from sqlalchemy import Engine, create_engine, text  # noqa: E402


@pytest.fixture(scope="session")
def pg_engine(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Engine]:
    """A migrated, seeded ``corridor_test`` database (created automatically)."""
    from alembic import command
    from alembic.config import Config

    from corridor.sandbox.db import ensure_database, test_database_url
    from corridor.sandbox.seed import seed

    url = test_database_url()
    try:
        ensure_database(url)
        engine = create_engine(url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # no Postgres reachable: skip database tests locally
        pytest.skip(f"postgres not available: {type(exc).__name__}")
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    os.environ.setdefault("PYTHONHASHSEED", "0")
    command.downgrade(cfg, "base")  # also exercises the downgrade path
    command.upgrade(cfg, "head")
    secrets_dir = tmp_path_factory.mktemp("secrets")
    seed(engine, secrets_dir=secrets_dir)
    engine.secrets_dir = secrets_dir  # type: ignore[attr-defined]
    yield engine
    engine.dispose()


@pytest.fixture
def sandbox(pg_engine: Engine) -> Engine:
    """The test database reseeded to the deterministic seed (and the safety tables cleared)."""
    from corridor.sandbox.seed import seed

    seed(pg_engine, secrets_dir=pg_engine.secrets_dir)  # type: ignore[attr-defined]
    return pg_engine
