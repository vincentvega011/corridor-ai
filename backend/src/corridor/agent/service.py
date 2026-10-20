"""Runs the agent graph for the API and the evaluation runner.

One ``AgentService`` owns a compiled graph. ``chat`` starts or continues a conversation turn;
``confirm`` and ``cancel`` resume a graph that is paused at the confirmation interrupt. Every method
takes ``user_id`` from the authenticated session and refuses a thread that belongs to someone else.
Events are streamed as they are emitted. A new chat message while a confirmation is pending discards
the pending proposal first (it is logged as a ``rejected`` event, nothing executes).
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import date
from typing import Any

import structlog
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from sqlalchemy.orm import Session

from corridor.ablation import Ablation
from corridor.agent import compose as composer
from corridor.agent.events import Emitter, EventSink
from corridor.agent.graph import Deps, build_graph
from corridor.confirm import ConfirmationService, ConfirmOutcome
from corridor.contracts.confirm import ConfirmationLevel
from corridor.contracts.events import AgentEvent, AgentEventType
from corridor.contracts.intent import LanguageTag

log = structlog.get_logger("corridor.agent.service")


class ThreadNotFound(Exception):
    """The thread does not exist or belongs to another user (the two are indistinguishable)."""


class NoPendingConfirmation(Exception):
    """There is nothing awaiting confirmation on this thread."""


@asynccontextmanager
async def memory_checkpointer() -> AsyncIterator[Any]:
    """In memory checkpointer (tests)."""
    yield InMemorySaver()


def sqlite_checkpointer(path: str) -> AbstractAsyncContextManager[Any]:
    """SQLite checkpointer (tests that restart the process)."""
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    return AsyncSqliteSaver.from_conn_string(path)


@asynccontextmanager
async def postgres_checkpointer(database_url: str) -> AsyncIterator[Any]:
    """Postgres checkpointer for dev and demo. ``database_url`` may be a SQLAlchemy URL."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    conn = database_url.replace("postgresql+psycopg://", "postgresql://")
    async with AsyncPostgresSaver.from_conn_string(conn) as saver:
        await saver.setup()
        yield saver


class AgentService:
    """Drives the graph and streams its events."""

    def __init__(
        self, deps: Deps, checkpointer: Any, clock: Callable[[], date] = date.today
    ) -> None:
        self.deps = deps
        self.graph = build_graph(deps, checkpointer)
        self.confirmations = ConfirmationService(deps.policy, deps.ledger, deps.secrets_dir)
        self.clock = clock
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[None]] = set()  # runs finish even if the client disconnects
        self._pending: dict[str, dict[str, dict[str, Any]]] = {}  # user -> thread -> pending card

    def _lock(self, thread_id: str) -> asyncio.Lock:
        """One lock per thread: confirm, cancel and chat on the same thread never interleave (INV-5)."""
        return self._locks.setdefault(thread_id, asyncio.Lock())

    @staticmethod
    def _config(thread_id: str, sink: EventSink | None = None) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id, "sink": sink}}

    async def _owned_state(self, thread_id: str, user_id: str) -> Any:
        snap = await self.graph.aget_state(self._config(thread_id))
        if not snap.values or snap.values.get("user_id") != user_id:
            raise ThreadNotFound(thread_id)
        return snap

    @staticmethod
    def _awaiting(snap: Any) -> bool:
        return tuple(snap.next) == ("await_confirmation",)

    async def _stream(
        self, graph_input: Any, thread_id: str, awaiting: str | None = None
    ) -> AsyncIterator[AgentEvent]:
        sink = EventSink()
        config = self._config(thread_id, sink)

        async def run() -> None:
            try:
                async with self._lock(
                    thread_id
                ):  # check and resume are one step per thread (INV-5)
                    if awaiting is not None:
                        pending = self._awaiting(
                            await self.graph.aget_state(self._config(thread_id))
                        )
                        if not pending:
                            if awaiting == "must":
                                raise NoPendingConfirmation(thread_id)
                            return
                    await self.graph.ainvoke(graph_input, config)
            finally:
                sink.close()

        task = asyncio.create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        try:
            while (event := await sink.queue.get()) is not None:
                yield event
            await task
        except NoPendingConfirmation:
            raise
        except (
            Exception
        ) as exc:  # fail closed: the stream ends with an error event, never a partial success
            log.error("agent_run_failed", error=type(exc).__name__)
            yield AgentEvent(
                type=AgentEventType.ERROR, trace_id="unknown", seq=0, ts=_now(),
                data={"reason": "internal_error", "thread_id": thread_id},
            )  # fmt: skip
        # No cancel on exit: a run that has already committed a transfer must finish and checkpoint
        # (a client disconnect must not leave the state at "awaiting" after money moved).

    async def _refresh_pending(self, user_id: str, thread_id: str) -> None:
        """Remember which proposal, if any, a thread is waiting on (read by GET /proposals/pending)."""
        try:
            snap = await self.graph.aget_state(self._config(thread_id))
        except (
            Exception
        ):  # a failed refresh only hides a card from the list; nothing executes from here
            return
        mine = self._pending.setdefault(user_id, {})
        if not snap.values or snap.values.get("user_id") != user_id or not self._awaiting(snap):
            mine.pop(thread_id, None)
            return
        v = snap.values
        mine[thread_id] = {
            "proposal_hash": v.get("proposal_hash"), "kind": "chat", "thread_id": thread_id,
            "required_level": v.get("required_level"), "card": v.get("proposal_card") or {},
            "risk": v.get("risk"),
        }  # fmt: skip

    def pending_proposals(self, user_id: str) -> list[dict[str, Any]]:
        """The caller's own pending chat proposals, newest thread last (never another user's)."""
        return list(self._pending.get(user_id, {}).values())

    async def chat(
        self,
        user_id: str,
        text: str,
        thread_id: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        forwarded: str | None = None,
        structured: dict[str, Any] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """One conversation turn. A new thread id is created when none is given."""
        if thread_id is None:
            thread_id = uuid.uuid4().hex
        else:
            existing = await self.graph.aget_state(self._config(thread_id))
            snap = await self._owned_state(thread_id, user_id) if existing.values else None
            if snap is not None and self._awaiting(
                snap
            ):  # a new message discards a pending proposal; nothing executes
                async for ev in self._stream(Command(resume={"action": "cancel"}), thread_id, "if"):
                    yield ev
        graph_input = {
            "thread_id": thread_id, "user_id": user_id, "trace_id": uuid.uuid4().hex, "next_seq": 0,
            "text": text, "attachments": attachments or [], "forwarded": forwarded,
            "today": self.clock().isoformat(),
        }  # fmt: skip
        # Always written, None for a typed turn: the checkpoint keeps every key, so a button turn must not leak into
        # the next typed turn of the same thread (a stale card for words the user never typed).
        graph_input["structured"] = structured  # a button flow: no model parses or plans it
        async for ev in self._stream(graph_input, thread_id):
            yield ev
        await self._refresh_pending(user_id, thread_id)

    async def confirm(
        self, user_id: str, thread_id: str, proposal_hash: str | None, totp: str | None = None
    ) -> AsyncIterator[AgentEvent]:
        """Confirm the pending proposal the user was shown.

        The hash must equal the stored proposal hash (INV-5). The confirmation service then checks
        the safety lock and, when policy requires MFA, the TOTP code, and mints a single use grant
        bound to that exact hash. Only then is the graph resumed, and the executor consumes the
        grant. A wrong code leaves the proposal pending so the user can retry; five wrong codes lock.
        """
        snap = await self._owned_state(thread_id, user_id)
        if not self._awaiting(snap):
            raise NoPendingConfirmation(thread_id)
        values = snap.values
        stored = values.get("proposal_hash")
        resume: dict[str, Any] = {"action": "confirm", "proposal_hash": proposal_hash}
        if self.deps.ablations.has(
            Ablation.NO_GRANTS
        ):  # evaluation only: no hash binding (ADR 0010)
            proposal_hash = str(stored) if stored else proposal_hash
        if proposal_hash and proposal_hash == stored:
            required = ConfirmationLevel(
                values.get("required_level") or "mfa"
            )  # unknown: strongest
            outcome = await asyncio.to_thread(
                self._mint, user_id, thread_id, str(stored), required, totp, values
            )
            if not outcome.ok or outcome.grant is None:
                async for ev in self._refuse(thread_id, values, outcome):
                    yield ev
                return
            resume["grant_id"] = outcome.grant.grant_id
        async for ev in self._stream(Command(resume=resume), thread_id, "must"):
            yield ev
        await self._refresh_pending(user_id, thread_id)

    def _mint(
        self,
        user_id: str,
        thread_id: str,
        proposal_hash: str,
        required: ConfirmationLevel,
        totp: str | None,
        values: dict[str, Any],
    ) -> ConfirmOutcome:
        with Session(self.deps.engine) as session, session.begin():
            return self.confirmations.confirm(
                session, user_id=user_id, proposal_hash=proposal_hash, required=required,
                totp=totp, now=self.deps.now_fn(), trace_id=values["trace_id"],
                session_id=thread_id,
            )  # fmt: skip

    async def _refuse(
        self, thread_id: str, values: dict[str, Any], outcome: ConfirmOutcome
    ) -> AsyncIterator[AgentEvent]:
        """Tell the user why a confirmation was refused. The graph stays paused at the card."""
        frame = values.get("frame") or {}
        lang = LanguageTag(frame.get("language", "en"))
        deva = bool(frame.get("deva", False))
        em = Emitter(None, values["trace_id"], thread_id, values.get("next_seq", 0))
        reason = outcome.reason or "refused"
        key = {"mfa_required": "mfa_required", "mfa_failed": "mfa_failed", "locked": "locked"}.get(
            reason, "grant_failed"
        )
        if outcome.locked and key == "mfa_failed":
            key = "locked"
        data: dict[str, Any] = {"reason": reason, "locked": outcome.locked}
        if outcome.attempts_left is not None:
            data["attempts_left"] = outcome.attempts_left
        text = composer.template(key, lang, deva, self.deps.packs, left=outcome.attempts_left or 0)
        events = [em.emit(AgentEventType.REJECTED, data)]
        events.append(
            em.emit(
                AgentEventType.MESSAGE, {"text": text, "source": "template", "status": "pending"}
            )
        )
        # keep the numbering monotonic; "request_confirmation" is the node before the interrupt
        await self.graph.aupdate_state(
            self._config(thread_id), {"next_seq": em.seq}, as_node="request_confirmation"
        )
        for ev in events:
            yield ev

    async def unlock(self, user_id: str, totp: str | None) -> ConfirmOutcome:
        """Lift the safety lock after a valid TOTP code (MFA re-verification, INV-11)."""

        def run() -> ConfirmOutcome:
            with Session(self.deps.engine) as session, session.begin():
                return self.confirmations.unlock(
                    session, user_id=user_id, totp=totp, now=self.deps.now_fn(),
                    trace_id=uuid.uuid4().hex, session_id=None,
                )  # fmt: skip

        return await asyncio.to_thread(run)

    async def cancel(self, user_id: str, thread_id: str) -> AsyncIterator[AgentEvent]:
        """Discard a pending proposal (or a pending clarification)."""
        snap = await self._owned_state(thread_id, user_id)
        if self._awaiting(snap):
            async for ev in self._stream(Command(resume={"action": "cancel"}), thread_id, "if"):
                yield ev
        else:
            await self.graph.aupdate_state(self._config(thread_id), {"pending": None})
        await self._refresh_pending(user_id, thread_id)

    async def thread_exists(self, thread_id: str) -> bool:
        """Whether any state is stored for the thread id (used to keep ids unguessable by 404s)."""
        return bool((await self.graph.aget_state(self._config(thread_id))).values)

    async def state(self, user_id: str, thread_id: str) -> dict[str, Any]:
        """The checkpointed state of an owned thread (for tests and the console)."""
        return dict((await self._owned_state(thread_id, user_id)).values)


def _now() -> Any:
    from datetime import UTC, datetime

    return datetime.now(UTC)
