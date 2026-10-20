"""Runs READ tools for one user inside a Postgres READ ONLY transaction (INV-1)."""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Engine

from corridor.contracts.tools import ToolResult
from corridor.sandbox.db import read_only_session
from corridor.sandbox.read_tools import ToolContext, ToolError, call_tool


def run_read_tool(
    engine: Engine, user_id: str, name: str, args: dict[str, Any], today: date,
    *, raw_payee_names: bool = False, now: datetime | None = None,
) -> ToolResult:  # fmt: skip
    """Run one registered READ tool. ``user_id`` comes from the authenticated session, never from a model."""
    with read_only_session(engine) as session:
        ctx = ToolContext(
            user_id,
            session,
            today,
            f"call-{uuid.uuid4().hex[:12]}",
            now=now,
            raw_payee_names=raw_payee_names,
        )
        return call_tool(ctx, name, args)


__all__ = ["ToolError", "run_read_tool"]
