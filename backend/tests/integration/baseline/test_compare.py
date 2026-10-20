"""POST /compare: the same input through the baseline (left) and Corridor AI (right)."""

import json
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, text
from test_baseline import ToolGateway

from agent_helpers import TOKENS
from corridor.agent.service import AgentService
from corridor.api.app import create_app
from corridor.api.auth import TokenAuth
from corridor.api.compare import CompareRunner
from corridor.baseline import BaselineAgent
from corridor.config import AppEnv, Settings


def _app(env: AppEnv, service: AgentService, sandbox: Engine, runner: CompareRunner | None) -> Any:
    return create_app(
        settings=Settings(app_env=env, frontend_origin="http://localhost:3000"), service=service, engine=sandbox,
        auth=TokenAuth(TOKENS), today=lambda: date(2026, 10, 10), compare=runner,
    )  # fmt: skip


@pytest.fixture
async def comp(
    service: AgentService, sandbox: Engine, baseline_engine: Engine
) -> AsyncIterator[tuple[httpx.AsyncClient, ToolGateway]]:
    gw = ToolGateway(
        [
            [
                (
                    "transfer",
                    {"payee_or_account": "pay_priya_parents", "amount": 50, "currency": "AUD"},
                )
            ],
            "Sent.",
        ]
    )
    runner = CompareRunner(
        BaselineAgent(baseline_engine, gw, secrets_dir=baseline_engine.secrets_dir), baseline_engine
    )  # type: ignore[arg-type, attr-defined]
    app = _app(AppEnv.DEV, service, sandbox, runner)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 5000))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
        yield c, gw


def _items(body: str) -> list[dict[str, Any]]:
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


async def test_compare_streams_both_sides_tagged_and_isolated(
    comp: Any, sandbox: Engine, baseline_engine: Engine
) -> None:
    client, _ = comp
    with sandbox.connect() as c:
        before = c.execute(text("SELECT count(*) FROM transfers")).scalar()
    r = await client.post(
        "/compare",
        json={"message": "send 50 AUD to baba"},
        headers={"Authorization": f"Bearer {TOKENS['usr_priya']}"},
    )
    assert r.status_code == 200
    items = _items(r.text)
    sides = {i["side"] for i in items}
    assert sides == {"left", "right"}
    assert any(
        i["side"] == "left" and i["type"] == "executed" for i in items
    )  # the naive agent just pays
    right = [i["type"] for i in items if i["side"] == "right"]
    assert "confirm_request" in right and "executed" not in right  # Corridor AI waits for the user
    with baseline_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM transfers WHERE id LIKE 'btr_%'")).scalar() == 1
    with sandbox.connect() as c:
        assert (
            c.execute(text("SELECT count(*) FROM transfers")).scalar() == before
        )  # the sandbox is untouched


async def test_chat_never_runs_the_baseline(comp: Any, baseline_engine: Engine) -> None:
    client, gw = comp
    r = await client.post(
        "/chat",
        json={"message": "send 50 AUD to baba"},
        headers={"Authorization": f"Bearer {TOKENS['usr_priya']}"},
    )
    assert r.status_code == 200
    assert all(
        role.value != "planner" or not tools for role, _, tools in gw.calls
    )  # the baseline gateway saw nothing
    with baseline_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM transfers WHERE id LIKE 'btr_%'")).scalar() == 0


async def test_compare_needs_a_valid_token(comp: Any) -> None:
    client, _ = comp
    assert (await client.post("/compare", json={"message": "hi"})).status_code in (401, 403)


def test_the_compare_endpoint_is_refused_outside_dev_and_demo(
    service: AgentService, sandbox: Engine, baseline_engine: Engine
) -> None:
    runner = CompareRunner(
        BaselineAgent(baseline_engine, ToolGateway([]), secrets_dir=baseline_engine.secrets_dir),
        baseline_engine,
    )  # type: ignore[arg-type, attr-defined]
    for env in (AppEnv.TEST, AppEnv.EVAL):
        with pytest.raises(RuntimeError):
            _app(env, service, sandbox, runner)
    app = _app(AppEnv.TEST, service, sandbox, None)  # no runner: no route
    assert "/compare" not in {r.path for r in app.routes}
