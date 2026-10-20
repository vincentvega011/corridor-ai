"""With APP_ENV unset the API is a production API: no dev route, no sandbox authenticator, no demo features (P4-2)."""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from agent_helpers import TOKENS
from corridor.agent.service import AgentService
from corridor.api.app import create_app
from corridor.api.auth import TokenAuth
from corridor.config import AppEnv, Settings


def hdr(user: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[user]}"}


def test_the_default_environment_is_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    assert Settings(_env_file=None).app_env is AppEnv.PRODUCTION  # type: ignore[call-arg]


async def test_with_app_env_unset_every_sandbox_route_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, service: AgentService, sandbox
) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    app = create_app(
        settings=settings, service=service, engine=sandbox, auth=TokenAuth(TOKENS),
        today=lambda: date(2026, 10, 10),
    )  # fmt: skip
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
        h = hdr("usr_priya")
        for path in ("/demo/authenticator", "/dev/totp", "/dev/chat"):
            assert (await c.get(path, headers=h)).status_code == 404, path
        # officer routes need an officer secret that a production API does not load
        assert (await c.get("/officer/ledger", headers=h)).status_code in (401, 404)
        # the apps ask /clock before showing the persona switcher or the authenticator panel
        clock = (await c.get("/clock")).json()
        assert clock["sandbox"] is False and clock["demo"] is False
