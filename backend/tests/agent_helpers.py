"""Helpers shared by agent and API tests."""

from collections.abc import AsyncIterator
from datetime import date
from typing import Any

from sqlalchemy import Engine, text

from corridor.confirm import current_code
from corridor.contracts.events import AgentEvent

TODAY = date(2026, 10, 10)
TOKENS = {
    "usr_priya": "tok_priya_test",
    "usr_ramesh": "tok_ramesh_test",
    "usr_anil": "tok_anil_test",
}


async def collect(stream: AsyncIterator[AgentEvent]) -> list[AgentEvent]:
    return [ev async for ev in stream]


def types(events: list[AgentEvent]) -> list[str]:
    return [e.type.value for e in events]


def balance(engine: Engine, account_id: str) -> int:
    with engine.connect() as c:
        return int(
            c.execute(
                text("SELECT balance_minor FROM accounts WHERE id = :i"), {"i": account_id}
            ).scalar_one()
        )


def transfer_count(engine: Engine) -> int:
    with engine.connect() as c:
        return int(c.execute(text("SELECT count(*) FROM transfers")).scalar_one())


async def code_for(service: Any, user: str, thread: str) -> str | None:
    """The valid authenticator code if the pending proposal needs MFA, else None."""
    state = await service.state(user, thread)
    if state.get("required_level") != "mfa":
        return None
    return current_code(user, service.deps.now_fn(), service.deps.secrets_dir)
