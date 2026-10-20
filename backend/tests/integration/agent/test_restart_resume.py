"""A paused confirmation survives a process restart because the checkpointer is durable."""

from pathlib import Path

from sqlalchemy import Engine

from agent_fakes import ScriptedGateway
from agent_helpers import TODAY, balance, code_for, collect, types
from corridor.agent.graph import Deps
from corridor.agent.service import AgentService, sqlite_checkpointer
from corridor.agent.settings import load_corridors
from corridor.contracts.events import AgentEventType as ET
from corridor.nlu.lang_pack import load_packs


def make_service(engine: Engine, saver: object) -> AgentService:
    deps = Deps(
        engine, ScriptedGateway(TODAY), load_packs(), load_corridors(),  # type: ignore[arg-type]
        secrets_dir=engine.secrets_dir,  # type: ignore[attr-defined]
    )  # fmt: skip
    return AgentService(deps, saver, clock=lambda: TODAY)


async def test_interrupt_resumes_in_a_new_service_instance(sandbox: Engine, tmp_path: Path) -> None:
    db = str(tmp_path / "checkpoints.sqlite")
    before = balance(sandbox, "acc_ramesh_qar")
    async with sqlite_checkpointer(db) as saver:  # "process 1"
        events = await collect(
            make_service(sandbox, saver).chat("usr_ramesh", "Ama lai 20 hajar pathau")
        )
    req = next(e for e in events if e.type is ET.CONFIRM_REQUEST)
    thread = events[0].data["thread_id"]
    assert balance(sandbox, "acc_ramesh_qar") == before
    async with sqlite_checkpointer(db) as saver:  # "process 2": nothing shared but the file
        service = make_service(sandbox, saver)
        code = await code_for(service, "usr_ramesh", thread)
        done = await collect(service.confirm("usr_ramesh", thread, req.data["proposal_hash"], code))
    assert "executed" in types(done)
    assert balance(sandbox, "acc_ramesh_qar") < before
