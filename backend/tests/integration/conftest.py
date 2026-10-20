"""Fixtures for agent tests: a freshly seeded sandbox, a scripted gateway and a service factory."""

from collections.abc import AsyncIterator
from datetime import date

import httpx
import pytest
from sqlalchemy import Engine

from agent_fakes import ScriptedGateway
from agent_helpers import TODAY, TOKENS
from corridor.agent.graph import Deps
from corridor.agent.service import AgentService, memory_checkpointer
from corridor.agent.settings import load_corridors
from corridor.api.app import create_app
from corridor.api.auth import TokenAuth
from corridor.config import AppEnv, Settings
from corridor.nlu.lang_pack import load_packs


@pytest.fixture
def gateway() -> ScriptedGateway:
    return ScriptedGateway(TODAY)


@pytest.fixture
async def service(sandbox: Engine, gateway: ScriptedGateway) -> AsyncIterator[AgentService]:
    deps = Deps(
        sandbox, gateway, load_packs(), load_corridors(),  # type: ignore[arg-type]
        secrets_dir=sandbox.secrets_dir,  # type: ignore[attr-defined]
    )  # fmt: skip
    async with memory_checkpointer() as saver:
        yield AgentService(deps, saver, clock=lambda: TODAY)


@pytest.fixture
async def client(service: AgentService, sandbox: Engine) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(app_env=AppEnv.DEV, frontend_origin="http://localhost:3000")
    app = create_app(
        settings=settings, service=service, engine=sandbox, auth=TokenAuth(TOKENS),
        today=lambda: date(2026, 10, 10),
    )  # fmt: skip
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 5000))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
        yield c
