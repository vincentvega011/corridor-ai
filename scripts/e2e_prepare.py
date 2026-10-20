"""Prepare the sandbox database for the browser tests (P3-2): create it if needed, migrate, seed.

    cd backend && DATABASE_URL=postgresql+psycopg://corridor:corridor@localhost:5433/corridor_e2e \\
        uv run python ../scripts/e2e_prepare.py

A separate database (``corridor_e2e``) keeps the tests away from the developer's data. Synthetic data only.
"""

import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def main() -> int:
    url = make_url(os.environ["DATABASE_URL"])
    if not (url.database or "").endswith("_e2e"):
        print("refusing: the database name must end with _e2e", file=sys.stderr)
        return 2
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        if not c.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": url.database}).first():
            c.execute(text(f'CREATE DATABASE "{url.database}"'))
    from alembic import command
    from alembic.config import Config

    from corridor.sandbox.db import get_engine
    from corridor.sandbox.seed import seed

    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "backend", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "backend", "alembic"))
    command.upgrade(cfg, "head")
    seed(get_engine())
    print("e2e database ready:", url.database)
    return 0


if __name__ == "__main__":
    sys.exit(main())
