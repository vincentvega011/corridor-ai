"""Database access for the sandbox: engines, read-only sessions and the test database helper.

Security reason (INV-1): every read tool runs inside a Postgres ``READ ONLY`` transaction, so even
a bug or a hostile argument cannot write. The planner side only ever receives read-only sessions.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from corridor.config import get_settings

TEST_DB_NAME = "corridor_test"


def get_engine(url: str | None = None) -> Engine:
    """Create an engine for ``url`` (default: DATABASE_URL from settings)."""
    return create_engine(url or get_settings().database_url, pool_pre_ping=True)


@contextmanager
def read_only_session(engine: Engine) -> Iterator[Session]:
    """Yield a Session whose transaction is READ ONLY. Any write raises a database error."""
    conn = engine.connect().execution_options(postgresql_readonly=True)
    try:
        with Session(bind=conn) as session:
            yield session
    finally:
        conn.rollback()
        conn.close()


def test_database_url(url: str | None = None) -> str:
    """The URL of ``corridor_test`` on the same server as ``url`` (default DATABASE_URL)."""
    base = make_url(url or get_settings().database_url)
    return base.set(database=TEST_DB_NAME).render_as_string(hide_password=False)


def ensure_database(url: str) -> None:
    """Create the database named in ``url`` if it does not exist (connects via ``postgres``)."""
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": target.database}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    finally:
        admin.dispose()
