"""Side by side comparison: the same input through the naive baseline and through Corridor AI.

This is the only module of the running application that imports ``corridor.baseline`` (a test enforces it).
``POST /compare`` exists only when APP_ENV is dev or demo, and it is the only way the baseline is reachable
in a running server: ``/chat`` always runs the full agent. The baseline works on its own database
(``corridor_baseline``), never on the sandbox, and its events are tagged ``left`` while the full agent's
are tagged ``right`` (the demo layout).
"""

import asyncio
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine

from corridor.agent.service import AgentService
from corridor.baseline import BaselineAgent
from corridor.baseline.setup import prepare
from corridor.config import AppEnv, Settings
from corridor.contracts.events import AgentEvent
from corridor.reader.upload import UPLOAD_DIR

DOC_ID = re.compile(r"^[0-9a-f]{64}$")


class CompareRequest(BaseModel):
    """Body of POST /compare. Documents are the ids returned by POST /attachments."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(default="", max_length=2000)
    forwarded: str | None = Field(default=None, max_length=2000)
    attachments: list[str] = Field(default_factory=list, max_length=3)
    left_thread: str | None = Field(default=None, max_length=64, pattern=r"^[0-9a-f]{32}$")
    right_thread: str | None = Field(default=None, max_length=64, pattern=r"^[0-9a-f]{32}$")


@dataclass
class CompareRunner:
    """Holds the baseline agent (on its own database) next to the full service."""

    baseline: BaselineAgent
    baseline_engine: Engine

    async def stream(
        self, service: AgentService, user: str, body: CompareRequest
    ) -> AsyncIterator[dict[str, Any]]:
        """Both agents run at once; events arrive tagged with the side they belong to."""
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        blobs = [_stored_png(user, d) for d in body.attachments]

        async def left() -> None:
            try:
                async for ev in self.baseline.chat(
                    user, body.message, body.left_thread, blobs or None, body.forwarded
                ):
                    await queue.put(_tag("left", "baseline", ev))
            except Exception as exc:  # a crash on one side must not hide the other side
                await queue.put(
                    {
                        "side": "left",
                        "agent": "baseline",
                        "type": "error",
                        "data": {"reason": type(exc).__name__},
                    }
                )
            finally:
                await queue.put(None)

        async def right() -> None:
            try:
                docs = [{"doc_id": d} for d in body.attachments]
                async for ev in service.chat(
                    user, body.message, body.right_thread, docs or None, body.forwarded
                ):
                    await queue.put(_tag("right", "full", ev))
            except Exception as exc:
                await queue.put(
                    {
                        "side": "right",
                        "agent": "full",
                        "type": "error",
                        "data": {"reason": type(exc).__name__},
                    }
                )
            finally:
                await queue.put(None)

        tasks = [asyncio.create_task(left()), asyncio.create_task(right())]
        done = 0
        try:
            while done < 2:
                item = await queue.get()
                if item is None:
                    done += 1
                else:
                    yield item
        finally:
            await asyncio.gather(*tasks, return_exceptions=True)


def _tag(side: str, agent: str, ev: AgentEvent) -> dict[str, Any]:
    return {
        "side": side,
        "agent": agent,
        "type": ev.type.value,
        "seq": ev.seq,
        "trace_id": ev.trace_id,
        "data": ev.data,
    }


def _stored_png(user: str, doc_id: str) -> bytes:
    """The sanitised PNG that POST /attachments stored for this user's document."""
    if not DOC_ID.match(doc_id):
        raise HTTPException(status_code=400, detail="bad document id")
    safe = "".join(c for c in user if c.isalnum() or c in "_-")[:64] or "shared"
    path = UPLOAD_DIR / safe / f"{doc_id}.png"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="document not found")
    return path.read_bytes()


def build_compare(settings: Settings, gateway: Any, secrets_dir: Any = None) -> CompareRunner:
    """Prepare the baseline database and the baseline agent. Refused outside dev and demo."""
    if settings.app_env not in (AppEnv.DEV, AppEnv.DEMO):
        raise RuntimeError("the compare endpoint runs only with APP_ENV=dev or demo")
    engine = prepare(secrets_dir=secrets_dir)
    return CompareRunner(BaselineAgent(engine, gateway, secrets_dir=secrets_dir), engine)


def register(
    app: FastAPI, *, runner: CompareRunner, settings: Settings, current_user: Any, limiter: Any
) -> None:
    """Add POST /compare to ``app`` (dev and demo only)."""
    if settings.app_env not in (AppEnv.DEV, AppEnv.DEMO):
        raise RuntimeError("the compare endpoint runs only with APP_ENV=dev or demo")

    from fastapi import Depends

    @app.post("/compare")
    # each call makes many LLM calls; stay far below the shared 50/min
    @limiter.limit("4/minute")  # type: ignore[untyped-decorator]
    async def compare(
        request: Request, body: CompareRequest, user: str = Depends(current_user)
    ) -> StreamingResponse:
        if not (body.message.strip() or body.attachments or (body.forwarded or "").strip()):
            raise HTTPException(
                status_code=422, detail="send a message, a document or forwarded text"
            )
        for doc_id in body.attachments:
            _stored_png(user, doc_id)  # a missing document is a clean 404 before the stream starts
        service: AgentService = request.app.state.service

        async def sse() -> AsyncIterator[str]:
            async for item in runner.stream(service, user, body):
                yield f"event: compare\ndata: {json.dumps(item, ensure_ascii=False, default=str)}\n\n"

        return StreamingResponse(
            sse(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
