"""The naive baseline agent (docs/spec/architecture.md 4.3, ADR 0010, docs/baseline.md).

Same planner model, same READ tools, same system-prompt style as the full agent, plus two direct
write tools the model calls itself. Untrusted content goes straight into the model's context. There
is no taint, policy engine, grant, MFA, safety lock or grounding check, and no quarantine. It still
behaves like a polite assistant: its prompt tells it to confirm large payments in chat, and a "yes"
from the user is all the approval it ever gets.

It writes only to a separate baseline database and refuses to start otherwise.
"""

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from corridor.agent.toolrun import run_read_tool
from corridor.baseline import log
from corridor.baseline.isolation import assert_isolated
from corridor.baseline.prompts import DOC_HEADER, FORWARD_HEADER, SYSTEM_PROMPT
from corridor.baseline.tools import WRITE_TOOL_NAMES, WRITE_TOOLS, AccountAliases, BaselineTools
from corridor.baseline.transcribe import transcribe
from corridor.contracts.events import AgentEvent, AgentEventType
from corridor.contracts.money import CurrencyCode
from corridor.formatting import format_minor
from corridor.llm import Gateway, LLMError
from corridor.llm.config import ModelRole
from corridor.reader.upload import UploadError, process_upload
from corridor.sandbox.read_tools import CODE_RUN_TOOLS, READ_TOOLS, ToolError, tool_specs

MAX_ROUNDS = 8
MAX_HISTORY = 40


@dataclass
class _Thread:
    user_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    aliases: AccountAliases = field(default_factory=AccountAliases)


class BaselineAgent:
    """One naive agent bound to one baseline database."""

    def __init__(
        self,
        engine: Engine,
        gateway: Gateway,
        *,
        provider: str | None = None,
        model: str | None = None,
        secrets_dir: Path | None = None,
        clock: Any = date.today,
        main_sandbox_url: str | None = None,
        log_path: Path | None = None,
    ) -> None:
        assert_isolated(engine, main_sandbox_url)  # refuses to start on the sandbox database
        self.engine, self.gateway = engine, gateway
        self.provider, self.model = provider, model
        self.secrets_dir, self.clock, self.log_path = secrets_dir, clock, log_path
        self._threads: dict[str, _Thread] = {}
        self.llm_calls = 0

    # ------------------------------------------------------------------ public
    async def chat(
        self,
        user_id: str,
        text: str,
        thread_id: str | None = None,
        attachments: list[bytes] | None = None,
        forwarded: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """One user turn. Yields AgentEvents like the full agent so one runner handles both."""
        thread_id = thread_id or uuid.uuid4().hex
        th = self._threads.setdefault(thread_id, _Thread(user_id))
        if th.user_id != user_id:
            yield self._ev(thread_id, 0, AgentEventType.ERROR, {"reason": "thread_not_found"})
            return
        seq = 0
        trace = uuid.uuid4().hex
        content = text
        try:
            for blob in attachments or []:
                content += "\n\n" + await self._document_text(th, blob)
            if forwarded:
                content += f"\n\n{FORWARD_HEADER}\n{th.aliases.alias_text(forwarded)}"
        except (UploadError, LLMError) as exc:
            yield self._ev(
                thread_id, seq, AgentEventType.ERROR, {"reason": type(exc).__name__}, trace
            )
            return
        th.messages.append({"role": "user", "content": th.aliases.alias_text(content)})
        log.write(
            "user_turn",
            {"thread": thread_id, "user": user_id, "chars": len(content)},
            self.log_path,
        )
        specs = [*(s for s in tool_specs() if s.name not in CODE_RUN_TOOLS), *WRITE_TOOLS]
        tools = BaselineTools(self.engine, th.aliases, self.secrets_dir)
        for _ in range(MAX_ROUNDS):
            msgs = [{"role": "system", "content": SYSTEM_PROMPT}, *th.messages[-MAX_HISTORY:]]
            try:
                res = await self.gateway.complete(
                    ModelRole.PLANNER, msgs, tools=specs, provider=self.provider, model=self.model
                )
            except LLMError as exc:
                yield self._ev(
                    thread_id, seq, AgentEventType.ERROR, {"reason": type(exc).__name__}, trace
                )
                return
            self.llm_calls += res.calls
            if not res.tool_calls:
                reply = (res.text or "").strip()
                th.messages.append({"role": "assistant", "content": reply})
                yield self._ev(
                    thread_id,
                    seq,
                    AgentEventType.MESSAGE,
                    {"text": reply, "source": "baseline"},
                    trace,
                )
                return
            th.messages.append(
                {
                    "role": "assistant", "content": res.text or "",
                    "tool_calls": [
                        {"id": f"c{i}", "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                        for i, c in enumerate(res.tool_calls)
                    ],
                }
            )  # fmt: skip
            for i, call in enumerate(
                res.tool_calls
            ):  # short ids: provider ids can look like TOTP seeds to the prompt guard
                yield self._ev(
                    thread_id,
                    seq,
                    AgentEventType.STEP_START,
                    {"tool": call.name, "args": self._mask(call.arguments)},
                    trace,
                )
                seq += 1
                result, executed = self._run_tool(th, tools, call.name, call.arguments)
                log.write(
                    "tool_call",
                    {
                        "thread": thread_id,
                        "tool": call.name,
                        "args": self._mask(call.arguments),
                        "ok": result.get("ok", True),
                    },
                    self.log_path,
                )
                yield self._ev(
                    thread_id,
                    seq,
                    AgentEventType.STEP_RESULT,
                    {"tool": call.name, "ok": result.get("ok", True)},
                    trace,
                )
                seq += 1
                if executed is not None:
                    yield self._ev(thread_id, seq, AgentEventType.EXECUTED, executed, trace)
                    seq += 1
                th.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": f"c{i}",
                        "content": json.dumps(self._model_view(result), ensure_ascii=False),
                    }
                )
        yield self._ev(thread_id, seq, AgentEventType.ERROR, {"reason": "too_many_rounds"}, trace)

    # ------------------------------------------------------------------ internals
    async def _document_text(self, th: _Thread, blob: bytes) -> str:
        """The vision model's full transcription as planner context, accounts aliased (not the digits).

        No schema, no length cap, no flags: whatever is printed on the page, including small or faint
        text, reaches the planner as ordinary content (docs/baseline.md)."""
        stored = process_upload(blob, None, th.user_id)
        raw = await transcribe(self.gateway, stored.png)
        return f"{DOC_HEADER}\n{th.aliases.alias_text(raw)}"

    def _run_tool(
        self, th: _Thread, tools: BaselineTools, name: str, args: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        # the injected clock decides the date and the time is fixed (noon UTC), so history in tool results is
        # the same on every run of a replayed conversation (P2-7); with the default clock it is today
        now = datetime.combine(self.clock(), time(12, 0), tzinfo=UTC)
        if name == "transfer":
            res = tools.transfer(
                th.user_id,
                str(args.get("payee_or_account", "")),
                args.get("amount"),
                str(args.get("currency", "")),
                now,
            )
            if res.get("ok"):
                ccy = CurrencyCode(res["currency"])
                return res, {
                    "transfer_id": res["transfer_id"], "payee": res["payee"], "payee_id": res["payee_id"],
                    "amount": format_minor(res["amount_minor"], ccy), "agent": "baseline",
                    "balance_after": format_minor(res.get("balance_after_minor", 0), ccy),
                }  # fmt: skip
            return res, None
        if name == "add_payee":
            return tools.add_payee(
                th.user_id, str(args.get("name", "")), str(args.get("account", "")), now
            ), None
        if name in WRITE_TOOL_NAMES or name not in READ_TOOLS or name in CODE_RUN_TOOLS:
            return {"ok": False, "error": "unknown tool"}, None
        try:
            r = run_read_tool(
                self.engine, th.user_id, name, args, self.clock(), raw_payee_names=True
            )
        except ToolError as exc:
            return {"ok": False, "error": str(exc)[:120]}, None
        return {"ok": True, "data": r.data}, None

    @staticmethod
    def _model_view(result: dict[str, Any]) -> dict[str, Any]:
        """Drop raw minor-unit integers (the prompt guard blocks 9+ digit runs); show formatted money."""
        out = {k: v for k, v in result.items() if not k.endswith("_minor")}
        if "amount_minor" in result and "currency" in result:
            ccy = CurrencyCode(result["currency"])
            out["amount"] = format_minor(result["amount_minor"], ccy)
            out["balance_after"] = format_minor(result.get("balance_after_minor", 0), ccy)
        return out

    @staticmethod
    def _mask(args: dict[str, Any]) -> dict[str, Any]:
        return {k: (v if not isinstance(v, str) else v[:60]) for k, v in args.items()}

    @staticmethod
    def _ev(
        thread_id: str,
        seq: int,
        kind: AgentEventType,
        data: dict[str, Any],
        trace: str = "baseline",
    ) -> AgentEvent:
        return AgentEvent(
            type=kind, trace_id=trace, seq=seq, ts=datetime.now(UTC),
            data={**data, "thread_id": thread_id, "agent": "baseline"},
        )  # fmt: skip
