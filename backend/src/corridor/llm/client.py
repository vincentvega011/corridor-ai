"""The model gateway: one ``complete`` call for every role (docs/spec/modules.md 5.2).

Safety properties (INV-10, INV-12):
- The prompt guard runs before any request leaves the process, in live, record and replay mode.
- Every failure raises a typed LLMError. Nothing returns partial or default data.
- Structured output is validated with Pydantic; one repair retry, then LLMSchemaError.
- Only transient errors (connection, 429, 5xx) are retried, with backoff. Timeouts are not.
- Cost is integer micro-USD; no floats are produced or returned.
- Message content is never logged.
"""

import asyncio
import email.utils
import json
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import openai
import structlog
from pydantic import BaseModel, ConfigDict, ValidationError

from corridor.config import Settings, get_settings
from corridor.contracts.canonical import canonical_json
from corridor.contracts.tools import ToolSpec
from corridor.llm.cassette import CassetteStore, request_key, wire_messages
from corridor.llm.config import (
    ModelRole,
    ModelsConfig,
    ResolvedRole,
    api_key_for,
    load_models_config,
    resolve_role,
)
from corridor.llm.errors import (
    CassetteMiss,
    LLMConfigError,
    LLMError,
    LLMSchemaError,
    LLMTimeout,
    LLMUnavailable,
    QuotaExhausted,
)
from corridor.llm.guard import (
    SecretRegistry,
    redact_digit_runs,
    scan_messages,
    scan_text,
    scan_tools,
)
from corridor.llm.limiter import RateLimiter
from corridor.llm.local_server import Starter, ensure_running
from corridor.llm.tokens import estimate_messages, estimate_tokens

_TRUST_KEYS = frozenset({"provenance", "source_ref"})

log = structlog.get_logger("corridor.llm")

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
MAX_ERROR_CHARS = 500
MAX_TOKENS_CAP = 8192
MAX_TIMEOUT_CAP_S = 180
_SAFE_LOC = re.compile(r"[^A-Za-z0-9_.]")


class ToolCall(BaseModel):
    """A tool call requested by the model. Only a request: the gateway never runs tools."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    arguments: dict[str, Any]


class LLMResult(BaseModel):
    """Outcome of one ``complete`` call."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    role: ModelRole  # the role the caller asked for
    served_role: ModelRole  # the role that answered (differs after a failover)
    provider: str
    backend: str
    model: str
    text: str | None
    parsed: BaseModel | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cost_micro_usd: int
    cassette_key: str
    cassette_hit: bool
    calls: int = 1  # underlying requests made (2 when a repair retry was used)
    usage_estimated: bool = False  # token counts came from a local estimator, not the provider
    failover_from: str | None = None  # provider that failed before this answer
    failover_reason: str | None = None


def _cost_micro_usd(rr: ResolvedRole, input_tokens: int, output_tokens: int) -> int:
    """Integer cost, rounded up: tokens times micro-USD per 1M tokens over 1,000,000.

    Free routes (model id ending ``:free``) never accrue cost."""
    if rr.is_free:
        return 0
    p = rr.cfg.price
    total = input_tokens * p.input_micro_usd_per_m + output_tokens * p.output_micro_usd_per_m
    return -(-total // 1_000_000)


def _extract_json(text: str) -> str:
    """Strip think tags and code fences, then take the outermost JSON object."""
    t = _FENCE.sub("", _THINK.sub("", text).strip()).strip()
    start, end = t.find("{"), t.rfind("}")
    return t[start : end + 1] if start != -1 and end > start else t


def _schema_instruction(schema: type[BaseModel]) -> dict[str, str]:
    body = canonical_json(schema.model_json_schema())
    return {
        "role": "system",
        "content": (
            "Respond with a single JSON object that conforms to this JSON Schema. "
            "Integers only for numbers, no floats. Output JSON only, with no prose and "
            f"no code fences.\nSCHEMA_NAME: {schema.__name__}\nSCHEMA: {body}"
        ),
    }


def _has_trust_fields(schema: type[BaseModel]) -> bool:
    def walk(node: Any) -> bool:
        if isinstance(node, dict):
            props = node.get("properties")
            if isinstance(props, dict) and _TRUST_KEYS & set(props):
                return True
            return any(walk(v) for v in node.values())
        if isinstance(node, list):
            return any(walk(v) for v in node)
        return False

    return walk(schema.model_json_schema())


def _retry_after_s(headers: Any) -> int | None:
    """Parse a Retry-After header (seconds or HTTP date) into whole seconds, or None."""
    raw = headers.get("retry-after") if headers is not None else None
    if raw is None:
        return None
    raw = str(raw).strip()
    if raw.isdigit():
        return int(raw)
    try:
        when = email.utils.parsedate_to_datetime(raw)
        return max(0, int(when.timestamp() - time.time()))
    except (TypeError, ValueError):
        return None


def _no_float(token: str) -> float:
    raise ValueError("floats are not allowed in tool arguments")


def _error_summary(exc: ValidationError) -> str:
    """Location and message only. Never echo input values (they may be untrusted text)."""
    parts = [
        f"{_SAFE_LOC.sub('_', '.'.join(str(x) for x in e['loc']))}: {e['msg']}"
        for e in exc.errors()
    ]
    return "; ".join(parts)[:MAX_ERROR_CHARS]


class Gateway:
    """Calls model backends. One instance per process is typical; tests build their own."""

    def __init__(
        self,
        settings: Settings | None = None,
        config: ModelsConfig | None = None,
        registry: SecretRegistry | None = None,
        cassette_dir: Path | None = None,
        record: bool | None = None,
        replay: bool | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        transport: httpx.AsyncBaseTransport | None = None,
        limiter: RateLimiter | None = None,
        quota_path: Path | None = None,
        starter: Starter = ensure_running,
        record_roles: frozenset[str] = frozenset(),
    ) -> None:
        self.settings = settings or get_settings()
        self.config = config or load_models_config()
        self.registry = registry or SecretRegistry()
        for secret in (self.settings.planner_api_key, self.settings.tokenharbor_api_key):
            value = secret.get_secret_value()
            if len(value) >= 6:  # cloud API keys must never appear in any prompt
                self.registry.register(value)
        self.cassettes = CassetteStore(cassette_dir)
        self.record = self.settings.llm_record if record is None else record
        self.replay = self.settings.llm_replay if replay is None else replay
        # roles that go live and are re-recorded while everything else replays
        self.record_roles = record_roles
        self._sleep = sleep
        self._starter = starter
        self.limiter = limiter or RateLimiter(
            {name: p.limits for name, p in self.config.providers.items()},
            path=quota_path,
            sleep=sleep,
            max_wait_s=self.config.defaults.retry_after_max_s,
        )
        self._transport = transport  # tests inject a fake server here
        self._clients: dict[tuple[str, str], openai.AsyncOpenAI] = {}

    async def aclose(self) -> None:
        """Close HTTP clients."""
        for c in self._clients.values():
            await c.close()
        self._clients.clear()

    def _client(self, rr: ResolvedRole) -> openai.AsyncOpenAI:
        key = api_key_for(rr.provider, self.settings)
        ck = (rr.base_url, rr.provider_name)
        if ck not in self._clients:
            http_client = (
                httpx.AsyncClient(transport=self._transport, timeout=rr.cfg.timeout_s)
                if self._transport is not None
                else None
            )
            self._clients[ck] = openai.AsyncOpenAI(
                base_url=rr.base_url,
                api_key=key,
                timeout=rr.cfg.timeout_s,
                max_retries=0,
                http_client=http_client,  # type: ignore[arg-type]
            )
        return self._clients[ck]

    # ------------------------------------------------------------------ public API
    async def complete(
        self,
        role: ModelRole,
        messages: list[dict[str, Any]],
        schema: type[BaseModel] | None = None,
        tools: list[ToolSpec] | None = None,
        *,
        provider: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        timeout_s: int | None = None,
    ) -> LLMResult:
        """Run one model call for ``role``. Raises an LLMError subclass on any failure.

        If the role has ``fallback_roles`` and the provider fails with a quota, timeout,
        connection or 5xx error, the call fails over to the fallback role and the switch is
        logged. Passing ``provider`` or ``model`` forces exactly that backend and disables
        failover, so evaluation comparisons are clean. Prompt guard, schema, config and
        cassette errors never fail over.
        """
        forced = provider is not None or model is not None
        try:
            return await self._complete_once(
                role, messages, schema, tools, provider, model, max_tokens, timeout_s
            )
        except (LLMTimeout, LLMUnavailable) as exc:
            if forced or not self._failover_allowed(exc):
                raise
            primary = resolve_role(self.config, self.settings, role).provider_name
            for fb in self.config.roles[role].fallback_roles:
                reason = type(exc).__name__
                log.warning(
                    "llm_failover",
                    role=role.value,
                    from_provider=primary,
                    to_role=fb.value,
                    reason=reason,
                    status=getattr(exc, "status", None),
                )
                if not self.replay:
                    fb_rr = resolve_role(self.config, self.settings, fb)
                    if fb_rr.cfg.autostart is not None:
                        await self._starter(fb_rr.cfg.autostart, fb_rr.base_url)
                res = await self._complete_once(
                    fb, messages, schema, tools, None, None, max_tokens, timeout_s
                )
                return res.model_copy(
                    update={"role": role, "failover_from": primary, "failover_reason": reason}
                )
            raise

    @staticmethod
    def _failover_allowed(exc: LLMError) -> bool:
        if isinstance(exc, QuotaExhausted | LLMTimeout):
            return True
        return isinstance(exc, LLMUnavailable) and (exc.status is None or exc.status >= 500)

    async def _complete_once(
        self,
        role: ModelRole,
        messages: list[dict[str, Any]],
        schema: type[BaseModel] | None,
        tools: list[ToolSpec] | None,
        provider: str | None,
        model: str | None,
        max_tokens: int | None = None,
        timeout_s: int | None = None,
    ) -> LLMResult:
        started = time.monotonic()
        rr = resolve_role(self.config, self.settings, role, provider, model)
        if max_tokens is not None:  # explicit per call budget for batch jobs, bounded
            if not 1 <= max_tokens <= MAX_TOKENS_CAP:
                raise LLMConfigError(f"max_tokens must be 1 to {MAX_TOKENS_CAP}")
            new_cfg = rr.cfg.model_copy(update={"max_tokens": max_tokens})
            rr = rr.model_copy(update={"cfg": new_cfg})
        if timeout_s is not None:  # explicit per call deadline for batch jobs, bounded
            if not 1 <= timeout_s <= MAX_TIMEOUT_CAP_S:
                raise LLMConfigError(f"timeout_s must be 1 to {MAX_TIMEOUT_CAP_S}")
            rr = rr.model_copy(update={"cfg": rr.cfg.model_copy(update={"timeout_s": timeout_s})})
        if tools and not rr.cfg.supports_tools:
            raise LLMConfigError(f"role {role.value} does not support tools")
        if self.record and self.replay:
            raise LLMConfigError("LLM_RECORD and LLM_REPLAY cannot both be set")

        if schema is not None and _has_trust_fields(schema):
            # INV-4: a model must never be asked to emit provenance. Use contracts.llm_outputs.
            raise LLMConfigError(f"schema {schema.__name__} has provenance fields")
        msgs = [dict(m) for m in messages]
        if schema is not None:
            msgs.insert(0, _schema_instruction(schema))
        skip_first = 1 if schema is not None else 0  # our own instruction is not caller content

        text, calls_made = "", 0
        in_tok = out_tok = 0
        estimated = False
        hit = False
        key = ""
        tool_calls: tuple[ToolCall, ...] = ()
        parsed: BaseModel | None = None

        for attempt in range(2):  # attempt 1 is the single repair retry
            raw = await self._call(rr, msgs, schema, tools, skip_first)
            calls_made += 1
            text, tool_calls = raw["text"], raw["tool_calls"]
            in_tok += raw["input_tokens"]
            out_tok += raw["output_tokens"]
            estimated = estimated or raw.get("usage_estimated", False)
            hit, key = raw["hit"], raw["key"]
            if schema is None or tool_calls:
                break
            try:
                parsed = schema.model_validate_json(_extract_json(text))
                break
            except ValidationError as exc:
                if attempt == 1:
                    log.warning("llm_schema_failure", role=role.value, model=rr.model)
                    raise LLMSchemaError(
                        f"output did not match {schema.__name__} after repair"
                    ) from exc
                msgs = [
                    *msgs,
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": (
                            "Your previous output failed validation: "
                            f"{_error_summary(exc)}. Return the corrected JSON object only."
                        ),
                    },
                ]

        latency_ms = int((time.monotonic() - started) * 1000)
        cost = 0 if hit else _cost_micro_usd(rr, in_tok, out_tok)  # replays cost nothing
        log.info(
            "llm_call",
            role=role.value,
            provider=rr.provider_name,
            backend=rr.backend,
            model=rr.model,
            usage_estimated=estimated,
            latency_ms=latency_ms,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_micro_usd=cost,
            cassette="hit" if hit else ("miss" if self.replay else "off"),
            calls=calls_made,
        )
        return LLMResult(
            role=role,
            served_role=role,
            provider=rr.provider_name,
            backend=rr.backend,
            model=rr.model,
            text=text,
            parsed=parsed,
            tool_calls=tool_calls,
            latency_ms=latency_ms,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_micro_usd=cost,
            cassette_key=key,
            cassette_hit=hit,
            calls=calls_made,
            usage_estimated=estimated,
        )

    # ------------------------------------------------------------------ one request
    async def _call(
        self,
        rr: ResolvedRole,
        messages: list[dict[str, Any]],
        schema: type[BaseModel] | None,
        tools: list[ToolSpec] | None,
        skip_first: int = 0,
    ) -> dict[str, Any]:
        # INV-12: before anything leaves the process, and also in replay and record mode
        scan_messages(messages, self.registry, skip_first)
        scan_tools(tools or [], self.registry)
        try:
            key = request_key(
                rr.role.value,
                rr.model,
                messages,
                schema.__name__ if schema else None,
                [t.model_dump(mode="json") for t in tools or []],
                rr.cfg.temperature_milli,
                {
                    "provider": rr.provider_name,
                    "max_tokens": rr.cfg.max_tokens,
                    "extra_body": rr.cfg.extra_body,
                    "structured_mode": rr.cfg.structured_mode,
                },
            )
        except (TypeError, ValueError) as exc:
            raise LLMConfigError("messages must be plain JSON without floats") from exc
        if self.replay and rr.role.value not in self.record_roles:
            rec = self.cassettes.read(rr.role.value, key)  # raises CassetteMiss
            try:
                text, n_in, n_out = rec["text"], rec["input_tokens"], rec["output_tokens"]
                ok = (
                    isinstance(text, str)
                    and all(type(n) is int and n >= 0 for n in (n_in, n_out))
                    and isinstance(rec["tool_calls"], list)
                )
                if not ok:
                    raise CassetteMiss(key, rr.role.value)
                return {
                    "text": text,
                    "tool_calls": self._check_tool_names(
                        tuple(ToolCall.model_validate(t) for t in rec["tool_calls"]), tools
                    ),
                    "input_tokens": n_in,
                    "output_tokens": n_out,
                    "usage_estimated": bool(rec.get("usage_estimated", False)),
                    "hit": True,
                    "key": key,
                }
            except (KeyError, TypeError, ValueError, ValidationError) as exc:
                raise CassetteMiss(key, rr.role.value) from exc

        raw = await self._request_with_retries(rr, messages, schema, tools)
        raw["tool_calls"] = self._check_tool_names(raw["tool_calls"], tools)
        raw["hit"], raw["key"] = False, key
        if self.record or rr.role.value in self.record_roles:
            # Cassettes are committed to the repository: never record a response that leaks.
            # A reader response necessarily carries the account numbers printed on the synthetic
            # fixtures (data/fixtures). For the reader only, long digit runs are masked before the
            # scan, so key and seed shapes and registered non-numeric secrets are still caught.
            # Never record reader cassettes from a document that is not a synthetic fixture.
            scanned = redact_digit_runs(raw["text"]) if rr.role is ModelRole.READER else raw["text"]
            scan_text(scanned, self.registry)
            for tc in raw["tool_calls"]:
                scan_text(json.dumps(tc.arguments, ensure_ascii=False), self.registry)
            self.cassettes.write(
                rr.role.value,
                key,
                {
                    "text": raw["text"],
                    "tool_calls": [t.model_dump() for t in raw["tool_calls"]],
                    "input_tokens": raw["input_tokens"],
                    "output_tokens": raw["output_tokens"],
                    "usage_estimated": raw["usage_estimated"],
                },
            )
        return raw

    def _body(
        self,
        rr: ResolvedRole,
        messages: list[dict[str, Any]],
        schema: type[BaseModel] | None,
        tools: list[ToolSpec] | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": rr.model,
            "messages": wire_messages(messages),
            "max_tokens": rr.cfg.max_tokens,
            "temperature": rr.cfg.temperature_milli / 1000,
        }
        if rr.cfg.extra_body:
            body["extra_body"] = dict(rr.cfg.extra_body)
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.args_schema or {"type": "object", "properties": {}},
                    },
                }
                for t in tools
            ]
        elif schema is not None and rr.cfg.structured_mode == "json_object":
            body["response_format"] = {"type": "json_object"}
        elif schema is not None and rr.cfg.structured_mode == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                    "strict": True,
                },
            }
        return body

    async def _request_with_retries(
        self,
        rr: ResolvedRole,
        messages: list[dict[str, Any]],
        schema: type[BaseModel] | None,
        tools: list[ToolSpec] | None,
    ) -> dict[str, Any]:
        client = self._client(rr)
        body = self._body(rr, messages, schema, tools)
        retries = self.config.defaults.retries
        delay = self.config.defaults.backoff_ms / 1000
        ra_max = self.config.defaults.retry_after_max_s
        waited_retry_after = False
        last: Exception | None = None
        try:
            # One overall deadline covers every attempt, wait and backoff (fail closed within
            # the role timeout, spec 5.2), not a per attempt budget.
            async with asyncio.timeout(rr.cfg.timeout_s):
                attempt = 0
                while attempt <= retries:
                    await self.limiter.acquire(rr.provider_name)  # QuotaExhausted, never loops
                    try:
                        resp = await client.chat.completions.create(**body)
                        return self._finish(resp, rr, messages, tools)
                    except openai.APITimeoutError as exc:
                        raise LLMTimeout(f"{rr.role.value} request timed out") from exc
                    except openai.APIStatusError as exc:
                        if exc.status_code == 429:
                            ra = _retry_after_s(exc.response.headers)
                            # which limit was hit: only rate limit headers, never the body or a key
                            log.warning(
                                "provider_429",
                                provider=rr.provider_name,
                                retry_after_s=ra,
                                headers={
                                    k.lower(): v[:80]
                                    for k, v in exc.response.headers.items()
                                    if k.lower().startswith(
                                        ("x-ratelimit", "ratelimit", "retry-after")
                                    )
                                },
                            )
                            if waited_retry_after or ra is None or ra > ra_max:
                                self.limiter.cool_down(
                                    rr.provider_name, ra if ra is not None else 60
                                )
                                raise QuotaExhausted(
                                    f"{rr.provider_name} returned 429", retry_after_s=ra
                                ) from exc
                            waited_retry_after = True  # honour a short Retry-After exactly once
                            await self._sleep(ra)
                            continue  # does not consume a retry attempt
                        if exc.status_code >= 500:
                            last = exc
                        else:
                            raise LLMUnavailable(
                                f"{rr.role.value} request rejected with status {exc.status_code}",
                                status=exc.status_code,
                            ) from exc
                    except openai.APIConnectionError as exc:
                        last = exc
                    except openai.OpenAIError as exc:
                        raise LLMUnavailable(f"{rr.role.value} client error") from exc
                    attempt += 1
                    if attempt <= retries:
                        await self._sleep(delay)
                        delay *= 2
        except TimeoutError as exc:
            raise LLMTimeout(f"{rr.role.value} timed out after {rr.cfg.timeout_s}s") from exc
        status = getattr(last, "status_code", None)
        raise LLMUnavailable(f"{rr.role.value} unavailable after {retries + 1} attempts", status)

    def _finish(
        self,
        resp: Any,
        rr: ResolvedRole,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec] | None,
    ) -> dict[str, Any]:
        """Parse a response and fill in usage. Only free routes and local servers may omit it."""
        out = self._parse_response(resp)
        if out["input_tokens"] is None:
            if not (rr.provider.free_tier or rr.is_free or rr.backend == "local"):
                raise LLMSchemaError("paid cloud response without usage")
            reply_text = out["text"] + "".join(
                json.dumps(c.arguments, ensure_ascii=False) for c in out["tool_calls"]
            )
            prompt = estimate_messages(messages)
            if tools:
                prompt += estimate_tokens(json.dumps([t.model_dump(mode="json") for t in tools]))
            out["input_tokens"], out["output_tokens"] = prompt, estimate_tokens(reply_text)
            out["usage_estimated"] = True
        else:
            out["usage_estimated"] = False
        return out

    @staticmethod
    def _check_tool_names(
        calls: tuple[ToolCall, ...], tools: list[ToolSpec] | None
    ) -> tuple[ToolCall, ...]:
        """A returned tool call must name a tool we offered (a model can hallucinate names)."""
        allowed = {t.name for t in tools or []}
        if any(c.name not in allowed for c in calls):
            raise LLMSchemaError("model called a tool that was not offered")
        return calls

    @staticmethod
    def _parse_response(resp: Any) -> dict[str, Any]:
        try:
            choice = resp.choices[0]
            if choice.finish_reason in ("length", "content_filter"):
                raise ValueError("response was truncated or filtered")
            msg = choice.message
            calls: list[ToolCall] = []
            for tc in msg.tool_calls or []:
                args = json.loads(tc.function.arguments or "{}", parse_float=_no_float)
                if not isinstance(args, dict):
                    raise ValueError("tool arguments must be an object")
                calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
            usage = resp.usage
            return {
                "text": msg.content or "",
                "tool_calls": tuple(calls),
                "input_tokens": int(usage.prompt_tokens) if usage else None,
                "output_tokens": int(usage.completion_tokens) if usage else None,
            }
        except (AttributeError, IndexError, TypeError, ValueError) as exc:
            raise LLMSchemaError("malformed response from model backend") from exc


_default: Gateway | None = None


def get_gateway() -> Gateway:
    """Return the process-wide gateway, creating it on first use."""
    global _default
    if _default is None:
        from corridor.sandbox.vault import register_sandbox_secrets

        registry = SecretRegistry()
        register_sandbox_secrets(registry)  # INV-12: MFA seeds, HMAC key, full account numbers
        settings = get_settings()
        roles = frozenset(r.strip() for r in settings.llm_record_roles.split(",") if r.strip())
        if roles and settings.app_env.value not in ("dev", "demo", "test", "eval"):
            raise LLMConfigError(
                "LLM_RECORD_ROLES is for recording in dev, demo, test or eval only"
            )
        _default = Gateway(registry=registry, record_roles=roles)
    return _default


async def complete(
    role: ModelRole,
    messages: list[dict[str, Any]],
    schema: type[BaseModel] | None = None,
    tools: list[ToolSpec] | None = None,
    *,
    provider: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    timeout_s: int | None = None,
) -> LLMResult:
    """Module level shortcut for ``get_gateway().complete``."""
    return await get_gateway().complete(
        role,
        messages,
        schema,
        tools,
        provider=provider,
        model=model,
        max_tokens=max_tokens,
        timeout_s=timeout_s,
    )


__all__ = [
    "CassetteMiss",
    "Gateway",
    "LLMResult",
    "ToolCall",
    "complete",
    "get_gateway",
]
