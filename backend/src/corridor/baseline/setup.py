"""Create, migrate and seed the separate baseline database (``corridor_baseline``).

    cd backend && uv run python -m corridor.baseline.setup [--reset]

The seed is identical to the sandbox seed. ``--reset`` reseeds even when data exists. The URL comes from
BASELINE_DATABASE_URL, or DATABASE_URL with the database name replaced; it is refused if it equals the
sandbox URL (see ``assert_isolated``)."""

import sys
from pathlib import Path

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from corridor.baseline.isolation import assert_isolated
from corridor.config import REPO_ROOT, get_settings

BASELINE_DB = "corridor_baseline"


def baseline_url() -> str:
    """The baseline database URL: BASELINE_DATABASE_URL, else DATABASE_URL with another database name."""
    s = get_settings()
    if s.baseline_database_url:
        return s.baseline_database_url
    return make_url(s.database_url).set(database=BASELINE_DB).render_as_string(hide_password=False)


def prepare(url: str | None = None, reset: bool = False, secrets_dir: Path | None = None) -> Engine:
    """Ensure the database exists, is migrated and is seeded; return its engine."""
    from alembic import command
    from alembic.config import Config

    from corridor.sandbox.db import ensure_database
    from corridor.sandbox.seed import seed

    url = url or baseline_url()
    engine = create_engine(url)
    assert_isolated(engine)  # raises before anything is created if the URL is the sandbox
    ensure_database(url)
    cfg = Config(str(REPO_ROOT / "backend" / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    with engine.connect() as c:
        empty = c.execute(text("SELECT count(*) FROM users")).scalar_one() == 0
    if empty or reset:
        seed(engine, secrets_dir=secrets_dir)
    return engine


def main() -> None:
    engine = prepare(reset="--reset" in sys.argv)
    with engine.connect() as c:
        n = c.execute(text("SELECT count(*) FROM accounts")).scalar_one()
    print(f"baseline database ready: {engine.url.database} ({n} accounts)")


if __name__ == "__main__":
    main()
