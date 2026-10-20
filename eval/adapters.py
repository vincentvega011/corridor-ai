"""Drivers that play one simulated user against the full agent or the baseline, and the evaluation gateway.

``EvalGateway`` serves a request from a recorded cassette when one exists (so identical prompts across
modes and seeds cost nothing), and otherwise makes one live call that is recorded. Live cloud calls are
paced below the Token Harbor limits and are never allowed to trip ``QuotaExhausted``. Cloud roles are
forced to the cloud provider, so a failure never starts the local planner (the 16 GB memory rule).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import time
from collections import Counter
from pathlib import Path
from typing import Any

from corridor.agent.service import AgentService, NoPendingConfirmation
from corridor.baseline import BaselineAgent
from corridor.confirm import current_code
from corridor.llm import CassetteMiss, Gateway, ModelRole, SecretRegistry
from corridor.reader.service import read_upload
import simulator

CLOUD_ROLES = (ModelRole.PLANNER, ModelRole.COMPOSER, ModelRole.PARSER_FALLBACK)
MINUTE_HEADROOM = 42  # the limiter allows 50 a minute
HOUR_HEADROOM = 1380  # and 1,500 an hour


class EvalGateway:
    """Replay first, live and record on a miss, paced. Exposes the Gateway ``complete`` interface."""

    def __init__(
        self,
        registry: SecretRegistry,
        *,
        live: bool = True,
        planner_provider: str | None = "tokenharbor",
    ) -> None:
        self.replay = Gateway(registry=registry, record=False, replay=True)
        self.live = Gateway(registry=registry, record=True, replay=False)
        self.allow_live = live
        self.planner_provider = planner_provider
        self.counts: Counter[str] = Counter()
        self.fail_roles: frozenset[ModelRole] = frozenset()  # a case may simulate an unavailable provider
        self.waited_s = 0.0
        self.quota_errors = 0

    async def _pace(self) -> None:
        lim = self.live.limiter
        while True:
            m, h = lim.usage("tokenharbor")
            if m < MINUTE_HEADROOM and h < HOUR_HEADROOM:
                return
            nap = 4.0 if m >= MINUTE_HEADROOM else 30.0
            self.waited_s += nap
            await asyncio.sleep(nap)

    async def complete(
        self,
        role: ModelRole,
        messages: list[dict[str, Any]],
        schema: Any = None,
        tools: Any = None,
        **kw: Any,
    ) -> Any:
        if role in self.fail_roles:
            from corridor.llm import LLMUnavailable

            raise LLMUnavailable(f"{role.value} unavailable (simulated by the case)", status=503)
        if role in CLOUD_ROLES and kw.get("provider") is None and self.planner_provider:
            kw["provider"] = self.planner_provider  # no failover to a local model
        if (
            role is not ModelRole.PARSER
        ):  # the local parser is never recorded: it is not running in eval
            try:
                res = await self.replay.complete(role, messages, schema, tools, **kw)
                self.counts[f"replay:{role.value}"] += 1
                return res
            except CassetteMiss:
                pass
        if not self.allow_live and role is not ModelRole.PARSER:
            raise CassetteMiss("eval", role.value)
        if role in CLOUD_ROLES:
            await self._pace()
        res = await self.live.complete(role, messages, schema, tools, **kw)
        if role in CLOUD_ROLES:
            self.counts[f"cloud:{role.value}"] += res.calls
        return res

    @property
    def cloud_calls(self) -> int:
        return sum(v for k, v in self.counts.items() if k.startswith("cloud:"))

    def __getattr__(self, name: str) -> Any:  # limiter and the rest of the Gateway surface
        return getattr(self.live, name)


class FullDriver:
    """Plays turns against ``AgentService`` (the full agent and every ablation of it)."""

    def __init__(
        self, service: AgentService, engine: Any, gateway: Any, secrets_dir: Path, today: Any
    ) -> None:
        self.service, self.engine, self.gateway, self.secrets_dir, self.today = (
            service,
            engine,
            gateway,
            secrets_dir,
            today,
        )
        self.thread: str | None = None
        self.model = simulator.SCRIPTED
        self.known: simulator.Known | None = None

    async def turn(self, user: str, turn: Any, root: Path) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        docs: list[dict[str, Any]] = []
        try:
            for rel in turn.attach:
                res = await read_upload(
                    self.gateway,
                    self.engine,
                    user,
                    (root / rel).read_bytes(),
                    self.service.deps.now_fn(),
                    self.secrets_dir,
                )
                docs.append({"doc_id": res.doc_hash})
        except Exception as exc:
            events.append(_ev("error", {"reason": f"attachment_{type(exc).__name__}"}))
            return events
        async for ev in self.service.chat(
            user, turn.say, self.thread, attachments=docs or None, forwarded=turn.forwarded
        ):
            events.append(ev.model_dump(mode="json"))
        self.thread = self.thread or next(
            (e["data"]["thread_id"] for e in events if e["data"].get("thread_id")), None
        )
        for _step in range(
            MAX_PLAN_STEPS
        ):  # a multi step plan shows the next card after each confirmation
            cards = [e for e in events if e["type"] == "confirm_request"]
            if not (turn.confirm and cards and events[-1]["type"] == "confirm_request"):
                return events
            card = cards[-1]["data"]
            if (
                self.model == simulator.CAUTIOUS
                and self.known
                and not simulator.cautious_accepts_card(card, self.known)
            ):
                events.append(
                    _ev(
                        "user_declined",
                        {"proposal_hash": card["proposal_hash"], "user_model": self.model},
                    )
                )
                return events  # the careful user does not confirm and does not enter a code
            sent = "f" * 64 if turn.confirm == "wrong_hash" else card["proposal_hash"]
            for round_ in range(2 if turn.confirm == "twice" else 1):
                code = None
                if card.get("required_level") == "mfa":
                    code = {
                        "auto": current_code(user, self.service.deps.now_fn(), self.secrets_dir),
                        "wrong": "000000",
                        "none": None,
                    }[turn.totp]
                try:
                    async for ev in self.service.confirm(user, self.thread or "", sent, code):
                        events.append(ev.model_dump(mode="json"))
                except NoPendingConfirmation:
                    events.append(_ev("rejected", {"reason": "no_pending_confirmation"}))
            if turn.confirm is not True:
                break
            if card.get("required_level") == "mfa":  # the next code comes 31 seconds later (TOTP never repeats)
                prev = self.service.deps.now_fn
                self.service.deps.now_fn = lambda p=prev: p() + timedelta(seconds=31)  # type: ignore[misc]
        return events


MAX_PLAN_STEPS = 6  # most cards one scripted turn may confirm in a row


class BaselineDriver:
    """Plays turns against the naive agent. The user's "confirmation" is the word yes in chat."""

    def __init__(self, agent: BaselineAgent) -> None:
        self.agent = agent
        self.thread: str | None = None
        self.said: list[str] = []
        self.model = simulator.SCRIPTED
        self.known: simulator.Known | None = None

    async def _say(self, user: str, text: str, **kw: Any) -> list[dict[str, Any]]:
        self.said.append(text)
        out = [
            e.model_dump(mode="json") async for e in self.agent.chat(user, text, self.thread, **kw)
        ]
        self.thread = self.thread or next(
            (e["data"]["thread_id"] for e in out if e["data"].get("thread_id")), None
        )
        return out

    async def turn(self, user: str, turn: Any, root: Path) -> list[dict[str, Any]]:
        files = [(root / rel).read_bytes() for rel in turn.attach]
        events = await self._say(
            user, turn.say, attachments=files or None, forwarded=turn.forwarded
        )
        for _ in range(2 if turn.confirm == "twice" else 1):
            asked = (
                bool(events)
                and events[-1]["type"] == "message"
                and not any(e["type"] == "executed" for e in events)
            )
            if turn.confirm and asked and self._declines(events):
                events.append(_ev("user_declined", {"user_model": self.model}))
                break
            if turn.confirm and asked and self.model == simulator.FOOLED:
                # a deceived user answers the agent's question in character, again if it asks again
                for _round in range(simulator.FOOLED_ROUNDS):
                    question = str(events[-1]["data"].get("text", ""))
                    events += await self._say(user, simulator.fooled_answers(question))
                    asked = events[-1]["type"] == "message" and not any(
                        e["type"] == "executed" for e in events
                    )
                    if not asked:
                        break
            elif turn.confirm and asked:
                events += await self._say(user, "yes")
            elif turn.confirm == "twice":
                events += await self._say(user, "yes")
        return events

    def _declines(self, events: list[dict[str, Any]]) -> bool:
        if self.model != simulator.CAUTIOUS or self.known is None:
            return False
        question = str(events[-1]["data"].get("text", ""))
        return not simulator.cautious_accepts_question(question, self.known)


def _ev(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"type": kind, "data": data, "trace_id": "eval", "seq": 0}


async def timed(coro: Any) -> tuple[Any, float]:
    t = time.perf_counter()
    r = await coro
    return r, time.perf_counter() - t
