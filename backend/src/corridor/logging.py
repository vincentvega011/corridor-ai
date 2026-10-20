"""Structured JSON logging with a per-request trace_id.

The trace_id is held in a context variable so every log line emitted during a turn
carries it, which lets the console and the ledger be joined on trace_id.
"""

from __future__ import annotations

import logging
import sys
import uuid
from contextvars import ContextVar

import structlog

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


def new_trace_id() -> str:
    """Create a trace id and bind it to the current context."""
    tid = uuid.uuid4().hex
    _trace_id.set(tid)
    return tid


def set_trace_id(trace_id: str | None) -> None:
    """Bind an existing trace id (or clear it) in the current context."""
    _trace_id.set(trace_id)


def get_trace_id() -> str | None:
    """Return the trace id bound to the current context, if any."""
    return _trace_id.get()


def _add_trace_id(
    _logger: object, _method: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    tid = _trace_id.get()
    if tid is not None:
        event_dict["trace_id"] = tid
    return event_dict


def configure_logging(level: int = logging.INFO) -> None:
    """Configure structlog for JSON output on stdout."""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _add_trace_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.typing.FilteringBoundLogger:
    """Return a structlog logger."""
    return structlog.get_logger(name)  # type: ignore[no-any-return]
