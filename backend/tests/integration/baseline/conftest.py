"""A migrated, seeded baseline database (``corridor_test_baseline``), separate from the sandbox."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

BASELINE_DB = "corridor_test_baseline"


@pytest.fixture(scope="session")
def baseline_db(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Engine]:
    from alembic import command
    from alembic.config import Config

    from corridor.sandbox.db import ensure_database, test_database_url

    url = (
        make_url(test_database_url())
        .set(database=BASELINE_DB)
        .render_as_string(hide_password=False)
    )
    try:
        ensure_database(url)
        engine = create_engine(url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"postgres not available: {type(exc).__name__}")
    cfg = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    os.environ.setdefault("PYTHONHASHSEED", "0")
    command.upgrade(cfg, "head")
    engine.secrets_dir = tmp_path_factory.mktemp("bl_secrets")  # type: ignore[attr-defined]
    yield engine
    engine.dispose()


@pytest.fixture
def baseline_engine(baseline_db: Engine) -> Engine:
    from corridor.sandbox.seed import seed

    seed(baseline_db, secrets_dir=baseline_db.secrets_dir)  # type: ignore[attr-defined]
    return baseline_db
