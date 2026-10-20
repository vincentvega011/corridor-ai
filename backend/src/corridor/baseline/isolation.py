"""Isolation checks: the baseline can never touch the sandbox the full agent uses (ADR 0010)."""

from sqlalchemy import Engine
from sqlalchemy.engine import make_url

from corridor.config import get_settings


class BaselineIsolationError(Exception):
    """The baseline was pointed at a database it must not use."""


def baseline_database_name(url: str) -> str:
    """The database name in ``url`` ('' if none)."""
    return make_url(url).database or ""


def same_database(a: str, b: str) -> bool:
    """Whether two URLs point at the same server database (host, port and name; credentials ignored)."""
    ua, ub = make_url(a), make_url(b)
    return (ua.host or "localhost", ua.port or 5432, ua.database) == (
        ub.host or "localhost",
        ub.port or 5432,
        ub.database,
    )


def assert_isolated(engine: Engine, main_sandbox_url: str | None = None) -> None:
    """Raise unless ``engine`` is a separate baseline database.

    Two rules: its URL must not equal the main sandbox URL (the DATABASE_URL setting unless given), and
    its database name must contain ``baseline`` so a mistake in either direction is caught."""
    url = engine.url.render_as_string(hide_password=False)
    main = main_sandbox_url or get_settings().database_url
    if same_database(url, main):
        raise BaselineIsolationError("the baseline database must differ from the main sandbox")
    if "baseline" not in baseline_database_name(url):
        raise BaselineIsolationError("the baseline database name must contain 'baseline'")
