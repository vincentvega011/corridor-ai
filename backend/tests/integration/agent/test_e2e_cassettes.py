"""End to end through the real gateway in replay mode (recorded by ``LLM_RECORD=1 eval/run.py``).

No network and no API keys: every model call is served from ``eval/cassettes``. The model ids are
pinned here because they are part of the cassette key. If prompts change on purpose, re-record with
``cd backend && LLM_RECORD=1 uv run python ../eval/run.py --only t05_ramesh_send_ama_romanised``.
"""

from pathlib import Path

import pytest
from sqlalchemy import Engine

from agent_helpers import TODAY, balance, code_for, collect, types
from corridor.agent.graph import Deps
from corridor.agent.service import AgentService, memory_checkpointer
from corridor.agent.settings import load_corridors
from corridor.config import get_settings
from corridor.contracts.events import AgentEventType as ET
from corridor.llm import Gateway
from corridor.nlu.lang_pack import load_packs

CASSETTES = Path(__file__).resolve().parents[4] / "eval" / "cassettes"
PINNED = {
    "PLANNER_MODEL": "deepseek-v4.1-flash:free",
    "COMPOSER_MODEL": "deepseek-v4.1-flash:free",
    "PARSER_FALLBACK_MODEL": "deepseek-v4.1-flash:free",
    "PARSER_MODEL": "default_model",
    "LOCAL_PLANNER_MODEL": "default_model",
}


@pytest.fixture
def replay_gateway(monkeypatch: pytest.MonkeyPatch) -> Gateway:
    for k, v in PINNED.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    yield Gateway(replay=True, record=False, cassette_dir=CASSETTES)  # type: ignore[misc]
    get_settings.cache_clear()


async def test_ramesh_sends_to_ama_from_recorded_model_calls(
    sandbox: Engine, replay_gateway: Gateway
) -> None:
    deps = Deps(sandbox, replay_gateway, load_packs(), load_corridors())
    before = balance(sandbox, "acc_ramesh_qar")
    async with memory_checkpointer() as saver:
        service = AgentService(deps, saver, clock=lambda: TODAY)
        events = await collect(service.chat("usr_ramesh", "Ama lai 20 hajar pathau"))
        assert types(events)[-1] == "confirm_request", types(events)
        assert balance(sandbox, "acc_ramesh_qar") == before
        h = next(e for e in events if e.type is ET.CONFIRM_REQUEST).data["proposal_hash"]
        done = await collect(
            service.confirm(
                "usr_ramesh",
                events[0].data["thread_id"],
                h,
                await code_for(service, "usr_ramesh", events[0].data["thread_id"]),
            )
        )
    assert "executed" in types(done)
    assert balance(sandbox, "acc_ramesh_qar") < before
