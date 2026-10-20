"""A scripted stand-in for the model gateway, used by the agent and API tests.

It behaves like a reasonable model: the parser returns the keyword classifier's intent with no slots (the
deterministic normaliser supplies the evidence), the planner reads the context JSON it was given and
returns a matching draft, and the composer returns a fixed sentence. Behaviours can be overridden per
role to simulate attacks and failures. No network is used.
"""

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import BaseModel

from corridor.contracts.llm_outputs import ComposerOutput, ParserOutput, PlannerOutput
from corridor.llm import LLMTimeout, ModelRole
from corridor.nlu.lang_pack import load_packs
from corridor.nlu.normalise import normalise
from corridor.nlu.rules import classify_intent


@dataclass
class FakeResult:
    parsed: BaseModel | None
    model: str = "fake"
    provider: str = "fake"
    failover_from: str | None = None


class ScriptedGateway:
    """Behaves like Gateway for the roles the agent uses."""

    def __init__(self, today: date = date(2026, 10, 10)) -> None:
        self.today = today
        self.calls: list[tuple[ModelRole, list[dict[str, Any]]]] = []
        self.planner_override: Any = (
            None  # PlannerOutput | Exception | callable(ctx) -> PlannerOutput
        )
        self.composer_override: Any = None
        self.parser_override: Any = None  # ParserOutput | callable(text) -> ParserOutput
        self.reader_override: Any = None  # RawReaderOutput | callable(messages) -> RawReaderOutput
        self.packs = load_packs()

    def roles(self) -> list[ModelRole]:
        return [r for r, _ in self.calls]

    def prompts(self) -> list[str]:
        return [m["content"] for _, ms in self.calls for m in ms]

    async def complete(
        self,
        role: ModelRole,
        messages: list[dict[str, Any]],
        schema: Any = None,
        tools: Any = None,
        **kw: Any,
    ) -> FakeResult:
        self.calls.append((role, messages))
        if role in (ModelRole.PARSER, ModelRole.PARSER_FALLBACK):
            text = messages[-1]["content"].split("\nHints:")[0].removeprefix("Message: ")
            if self.parser_override is not None:
                o = (
                    self.parser_override(text)
                    if callable(self.parser_override)
                    else self.parser_override
                )
                if isinstance(o, Exception):
                    raise o
                return FakeResult(o)
            guess = classify_intent(text, None, self.packs, self.today).intent
            norm = normalise(text, self.today, self.packs)
            return FakeResult(
                ParserOutput(
                    language=norm.language,
                    intent=guess or "other",
                    slots={},
                    confidence_bps=9000,  # type: ignore[arg-type]
                )
            )
        if role is ModelRole.READER:
            from corridor.reader.model import RawMessage, RawReaderOutput

            o = self.reader_override
            o = o(messages) if callable(o) else o
            if isinstance(o, Exception):
                raise o
            return FakeResult(
                o
                if o is not None
                else RawReaderOutput(doc_type="message", message=RawMessage(sender_label="someone"))
            )
        if role in (ModelRole.PLANNER, ModelRole.LOCAL_PLANNER):
            ctx = json.loads(messages[-1]["content"])
            if self.planner_override is not None:
                o = (
                    self.planner_override(ctx)
                    if callable(self.planner_override)
                    else self.planner_override
                )
                if isinstance(o, Exception):
                    raise o
                return FakeResult(o)
            return FakeResult(self.default_plan(ctx))
        if role is ModelRole.COMPOSER:
            if self.composer_override is not None:
                if isinstance(self.composer_override, Exception):
                    raise self.composer_override
                return FakeResult(self.composer_override)
            return FakeResult(ComposerOutput(reply="Here is what I found."))
        raise LLMTimeout(f"unexpected role {role}")

    @staticmethod
    def default_plan(ctx: dict[str, Any]) -> PlannerOutput:
        action = ctx.get("action_to_propose")
        accounts = ctx.get("user_accounts", [])
        src = accounts[0]["account_id"] if accounts else "x"
        if action == "transfer":
            return PlannerOutput.model_validate(
                {
                    "steps": [{"id": "s1", "kind": "READ", "tool": "get_balances", "args": {}, "depends_on": []}],
                    "proposal": {"action": "transfer", "source_account_ref": src, "payee_ref": ctx["resolved_payee"]["payee_id"], "rationale": "send"},
                }
            )  # fmt: skip
        if action == "pay_invoice":
            return PlannerOutput.model_validate(
                {
                    "steps": [],
                    "proposal": {"action": "pay_invoice", "source_account_ref": src, "invoice_ref": ctx["open_invoice"]["invoice_id"], "rationale": "pay"},
                }
            )  # fmt: skip
        return PlannerOutput.model_validate({"steps": [], "proposal": None})
