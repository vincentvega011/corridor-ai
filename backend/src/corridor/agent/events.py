"""AgentEvent emission with a monotonically increasing ``seq`` per trace id.

Nodes get an ``Emitter`` that starts at the sequence number stored in the checkpointed state and
returns the next value, so numbering survives an interrupt and a process restart. Events never carry
secrets or full account numbers (INV-12): payees are shown as a name plus ``****1234``.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

from corridor.contracts.events import AgentEvent, AgentEventType


class EventSink:
    """Collects the events of one run and feeds an async consumer (the SSE stream)."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
        self.events: list[AgentEvent] = []

    def put(self, event: AgentEvent) -> None:
        self.events.append(event)
        self.queue.put_nowait(event)

    def close(self) -> None:
        self.queue.put_nowait(None)


class Emitter:
    """Emits events for one node execution."""

    def __init__(
        self, sink: EventSink | None, trace_id: str, thread_id: str, next_seq: int
    ) -> None:
        self.sink, self.trace_id, self.thread_id, self.seq = sink, trace_id, thread_id, next_seq

    def emit(self, type_: AgentEventType, data: dict[str, Any] | None = None) -> AgentEvent:
        event = AgentEvent(
            type=type_,
            trace_id=self.trace_id,
            seq=self.seq,
            ts=datetime.now(UTC),
            data={**(data or {}), "thread_id": self.thread_id},
        )
        self.seq += 1
        if self.sink is not None:
            self.sink.put(event)
        return event
