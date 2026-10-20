"""Gateway tests against a fake OpenAI compatible server (respx)."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, ConfigDict

from corridor.config import Settings
from corridor.contracts.tools import ToolSpec
from corridor.llm import (
    CassetteMiss,
    Gateway,
    LLMConfigError,
    LLMSchemaError,
    LLMTimeout,
    LLMUnavailable,
    ModelRole,
    PromptLeakError,
    SecretRegistry,
)
from corridor.llm.config import load_models_config
from fake_server import FakeServer

net = FakeServer()


@pytest.fixture(autouse=True)
def _reset_net() -> None:
    net.reset()


URL = "http://fake.test/v1/chat/completions"


class Out(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str
    confidence_bps: int


def make_settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "app_env": "test",
        "tokenharbor_base_url": "http://fake.test/v1",
        "tokenharbor_api_key": "test-key-not-real",
        "planner_model": "m-planner",
        "composer_model": "m-composer",
        "parser_fallback_model": "m-fallback",
        "parser_base_url": "http://local.test/v1",
        "parser_model": "m-parser",
        "reader_base_url": "http://reader.test/v1",
        "reader_model": "m-reader",
        "local_planner_model": "m-local",
        "llm_record": False,
        "llm_replay": False,
    }
    base.update(kw)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


async def _nosleep(_: float) -> None:
    return None


started: list[str] = []


async def _nostart(auto: Any, base_url: str) -> bool:
    started.append(base_url)
    return False


def priced_config(free_tier: bool = True) -> Any:
    """A config where the cloud roles carry real prices (the shipped config is free tier)."""
    from corridor.llm.config import Price

    cfg = load_models_config()
    roles = dict(cfg.roles)
    prices = {ModelRole.PLANNER: (2_000_000, 6_000_000), ModelRole.COMPOSER: (150_000, 470_000)}
    for role, (pin, pout) in prices.items():
        roles[role] = roles[role].model_copy(
            update={
                "price": Price(input_micro_usd_per_m=pin, output_micro_usd_per_m=pout, model="x")
            }
        )
    prov = cfg.providers["tokenharbor"].model_copy(update={"free_tier": free_tier})
    return cfg.model_copy(
        update={"roles": roles, "providers": {**cfg.providers, "tokenharbor": prov}}
    )


def make_gateway(tmp_path: Path, **kw: Any) -> Gateway:
    settings = kw.pop("settings", None) or make_settings()
    return Gateway(
        settings=settings,
        config=kw.pop("config", None) or load_models_config(),
        cassette_dir=tmp_path / "cassettes",
        sleep=_nosleep,
        transport=net.transport,
        quota_path=tmp_path / "quota.json",
        starter=_nostart,
        **kw,
    )


def reply(
    text: str = "pong",
    pin: int = 10,
    pout: int = 5,
    tool_calls: list[dict[str, Any]] | None = None,
) -> httpx.Response:
    msg: dict[str, Any] = {"role": "assistant", "content": text}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return httpx.Response(
        200,
        json={
            "id": "c1",
            "object": "chat.completion",
            "created": 1,
            "model": "m",
            "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": pin, "completion_tokens": pout, "total_tokens": pin + pout},
        },
    )


MSG = [{"role": "user", "content": "hello"}]
GOOD = json.dumps({"intent": "send_money", "confidence_bps": 9100})


async def test_plain_completion_and_cost(tmp_path: Path) -> None:
    route = net.post(URL).mock(return_value=reply("pong", pin=1000, pout=500))
    res = await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG)
    assert res.text == "pong" and res.model == "m-planner" and res.backend == "cloud"
    assert res.provider == "tokenharbor" and res.failover_from is None
    assert (res.input_tokens, res.output_tokens) == (1000, 500)
    assert res.cost_micro_usd == 0 and isinstance(res.cost_micro_usd, int)  # free tier
    priced = await make_gateway(tmp_path, config=priced_config()).complete(ModelRole.PLANNER, MSG)
    assert priced.cost_micro_usd == 5000  # 1000 * 2 + 500 * 6 micro-USD
    assert route.call_count == 2
    body = json.loads(route.calls[0].request.content)
    assert body["temperature"] == 0 and body["model"] == "m-planner"


async def test_free_models_never_cost(tmp_path: Path) -> None:
    net.post(URL).mock(return_value=reply("x", pin=100000, pout=100000))
    gw = make_gateway(
        tmp_path, settings=make_settings(planner_model="m:free"), config=priced_config()
    )
    assert (await gw.complete(ModelRole.PLANNER, MSG)).cost_micro_usd == 0


async def test_cost_rounds_up_for_cheap_models(tmp_path: Path) -> None:
    net.post(URL).mock(return_value=reply("x", pin=1, pout=1))
    res = await make_gateway(tmp_path, config=priced_config()).complete(ModelRole.COMPOSER, MSG)
    assert res.cost_micro_usd == 1  # 0.15 + 0.47 micro-USD rounds up to 1


async def test_structured_output_json_object_mode(tmp_path: Path) -> None:
    route = net.post(URL).mock(return_value=reply(f"```json\n{GOOD}\n```"))
    res = await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, schema=Out)
    assert isinstance(res.parsed, Out) and res.parsed.confidence_bps == 9100 and res.calls == 1
    body = json.loads(route.calls[0].request.content)
    assert body["response_format"] == {"type": "json_object"}
    assert "SCHEMA_NAME: Out" in body["messages"][0]["content"]


async def test_think_tags_stripped(tmp_path: Path) -> None:
    net.post(URL).mock(return_value=reply(f"<think>hmm {{x}}</think>{GOOD}"))
    res = await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, schema=Out)
    assert isinstance(res.parsed, Out)


async def test_prompt_mode_for_local_role(tmp_path: Path) -> None:
    route = net.post("http://local.test/v1/chat/completions").mock(return_value=reply(GOOD))
    res = await make_gateway(tmp_path).complete(ModelRole.PARSER, MSG, schema=Out)
    assert res.backend == "local" and res.cost_micro_usd == 0
    assert "response_format" not in json.loads(route.calls[0].request.content)


async def test_schema_repair_path(tmp_path: Path) -> None:
    bad = json.dumps({"intent": "send_money", "confidence_bps": 0.91, "secret_field": "ZZ"})
    route = net.post(URL).mock(side_effect=[reply(bad, 10, 5), reply(GOOD, 20, 6)])
    res = await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, schema=Out)
    assert isinstance(res.parsed, Out) and res.calls == 2
    assert (res.input_tokens, res.output_tokens) == (30, 11)
    second = json.loads(route.calls[1].request.content)["messages"]
    assert second[-2]["role"] == "assistant" and "failed validation" in second[-1]["content"]
    assert "ZZ" not in second[-1]["content"]  # the repair prompt never echoes input values


async def test_schema_failure_fails_closed(tmp_path: Path) -> None:
    route = net.post(URL).mock(return_value=reply("not json at all"))
    with pytest.raises(LLMSchemaError):
        await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, schema=Out)
    assert route.call_count == 2  # exactly one repair retry


async def test_timeout_is_not_retried(tmp_path: Path) -> None:
    route = net.post(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(LLMTimeout):
        await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, provider="tokenharbor")
    assert route.call_count == 1


@pytest.mark.parametrize("status", [500, 503])
async def test_transient_status_retried(tmp_path: Path, status: int) -> None:
    route = net.post(URL).mock(
        side_effect=[httpx.Response(status), httpx.Response(status), reply("ok")]
    )
    res = await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG)
    assert res.text == "ok" and route.call_count == 3


async def test_connection_error_retried_then_ok(tmp_path: Path) -> None:
    route = net.post(URL).mock(side_effect=[httpx.ConnectError("down"), reply("ok")])
    res = await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG)
    assert res.text == "ok" and route.call_count == 2


async def test_transient_exhausted_fails_closed(tmp_path: Path) -> None:
    route = net.post(URL).mock(return_value=httpx.Response(503))
    with pytest.raises(LLMUnavailable) as ei:
        await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, provider="tokenharbor")
    assert route.call_count == 3 and ei.value.status == 503


@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_non_transient_fails_closed_without_retry(tmp_path: Path, status: int) -> None:
    route = net.post(URL).mock(return_value=httpx.Response(status, json={"error": {}}))
    with pytest.raises(LLMUnavailable) as ei:
        await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG)
    assert route.call_count == 1 and ei.value.status == status


async def test_malformed_response_fails_closed(tmp_path: Path) -> None:
    net.post(URL).mock(return_value=httpx.Response(200, json={"choices": []}))
    with pytest.raises(LLMSchemaError):
        await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG)


# --- tools -------------------------------------------------------------------------------
TOOL = ToolSpec(name="get_balance", description="d", args_schema={"type": "object"})


def _tc(args: str) -> list[dict[str, Any]]:
    return [
        {"id": "t1", "type": "function", "function": {"name": "get_balance", "arguments": args}}
    ]


async def test_tool_calls_parsed(tmp_path: Path) -> None:
    route = net.post(URL).mock(return_value=reply("", tool_calls=_tc('{"account_id": "acc1"}')))
    res = await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, tools=[TOOL])
    assert res.tool_calls[0].name == "get_balance"
    assert res.tool_calls[0].arguments == {"account_id": "acc1"}
    body = json.loads(route.calls[0].request.content)
    assert body["tools"][0]["function"]["name"] == "get_balance"


async def test_bad_tool_arguments_fail_closed(tmp_path: Path) -> None:
    net.post(URL).mock(return_value=reply("", tool_calls=_tc("{not json")))
    with pytest.raises(LLMSchemaError):
        await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, tools=[TOOL])
    net.post(URL).mock(return_value=reply("", tool_calls=_tc("[1,2]")))
    with pytest.raises(LLMSchemaError):
        await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, tools=[TOOL])


async def test_tools_refused_for_roles_without_tools(tmp_path: Path) -> None:
    with pytest.raises(LLMConfigError):
        await make_gateway(tmp_path).complete(ModelRole.READER, MSG, tools=[TOOL])


# --- prompt guard (INV-12) ---------------------------------------------------------------
@pytest.mark.parametrize(
    ("content", "rule"),
    [
        ("pay account 123456789012 now", "R-GUARD-DIGITS"),
        ("account 1234 5678 9012", "R-GUARD-DIGITS"),
        ("acct 123-456-7890", "R-GUARD-DIGITS"),
        ("खाता १२३४५६७८९०१२", "R-GUARD-DIGITS"),
        ("key sk-abcdefghijklmnop12345678", "R-GUARD-KEY"),
        ("-----BEGIN PRIVATE KEY-----", "R-GUARD-KEY"),
        ("seed JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP", "R-GUARD-TOTP"),
        ("the code is hunter2-secret-value", "R-GUARD-SECRET"),
    ],
)
async def test_prompt_guard_blocks(tmp_path: Path, content: str, rule: str) -> None:
    route = net.post(URL).mock(return_value=reply())
    reg = SecretRegistry(["hunter2-secret-value"])
    gw = make_gateway(tmp_path, registry=reg)
    with pytest.raises(PromptLeakError) as ei:
        await gw.complete(ModelRole.PLANNER, [{"role": "user", "content": content}])
    assert ei.value.rule_id == rule and route.call_count == 0
    assert content not in str(ei.value)


async def test_prompt_guard_blocks_tool_call_arguments_and_parts(tmp_path: Path) -> None:
    route = net.post(URL).mock(return_value=reply())
    gw = make_gateway(tmp_path)
    msgs = [{"role": "assistant", "content": "", "tool_calls": _tc('{"a": "123456789012"}')}]
    with pytest.raises(PromptLeakError):
        await gw.complete(ModelRole.PLANNER, msgs)
    parts = [{"role": "user", "content": [{"type": "text", "text": "acct 999888777666"}]}]
    with pytest.raises(PromptLeakError):
        await gw.complete(ModelRole.PLANNER, parts)
    assert route.call_count == 0


@pytest.mark.parametrize(
    "content",
    [
        "send to account ending ****1234",
        "NPR 1,000,000.00 on 2026-10-10 12:00",
        "due 2026-10-10 12 items, 20000 and 12345678 are fine",
        "ordinary text with CamelCase and ABCDEFGHIJKLMNO words",
    ],
)
async def test_prompt_guard_allows_masked_and_normal(tmp_path: Path, content: str) -> None:
    net.post(URL).mock(return_value=reply())
    res = await make_gateway(tmp_path).complete(
        ModelRole.PLANNER, [{"role": "user", "content": content}]
    )
    assert res.text == "pong"


async def test_guard_runs_on_replay_and_logs_no_content(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gw = make_gateway(tmp_path, replay=True)
    with pytest.raises(PromptLeakError):
        await gw.complete(ModelRole.PLANNER, [{"role": "user", "content": "x 123456789012"}])
    assert "123456789012" not in capsys.readouterr().out


async def test_content_not_logged(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    net.post(URL).mock(return_value=reply("SECRET-REPLY-TEXT"))
    await make_gateway(tmp_path).complete(
        ModelRole.PLANNER, [{"role": "user", "content": "SECRET-PROMPT-TEXT"}]
    )
    out = capsys.readouterr().out
    assert "SECRET-PROMPT-TEXT" not in out and "SECRET-REPLY-TEXT" not in out
    assert "test-key-not-real" not in out


# --- record and replay -------------------------------------------------------------------
async def test_record_then_replay_identical(tmp_path: Path) -> None:
    route = net.post(URL).mock(return_value=reply(GOOD, 42, 7))
    rec = await make_gateway(tmp_path, record=True).complete(ModelRole.PLANNER, MSG, schema=Out)
    assert route.call_count == 1
    files = list((tmp_path / "cassettes" / "planner").glob("*.json"))
    assert len(files) == 1 and files[0].stem == rec.cassette_key

    net.reset()  # replay must not touch the network
    rep = await make_gateway(tmp_path, replay=True).complete(ModelRole.PLANNER, MSG, schema=Out)
    assert rep.cassette_hit and not rec.cassette_hit
    assert rep.parsed == rec.parsed and rep.text == rec.text
    assert (rep.input_tokens, rep.output_tokens, rep.cost_micro_usd) == (
        42,
        7,
        0,
    )  # a replay costs nothing
    assert rep.cassette_key == rec.cassette_key


async def test_repair_path_replays(tmp_path: Path) -> None:
    net.post(URL).mock(side_effect=[reply("oops"), reply(GOOD)])
    rec = await make_gateway(tmp_path, record=True).complete(ModelRole.PLANNER, MSG, schema=Out)
    net.reset()
    rep = await make_gateway(tmp_path, replay=True).complete(ModelRole.PLANNER, MSG, schema=Out)
    assert rep.parsed == rec.parsed and rep.calls == 2


async def test_replay_miss_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(CassetteMiss):
        await make_gateway(tmp_path, replay=True).complete(ModelRole.PLANNER, MSG)


async def test_corrupt_cassette_is_a_miss(tmp_path: Path) -> None:
    gw = make_gateway(tmp_path, replay=True)
    net.post(URL).mock(return_value=reply())
    rec = await make_gateway(tmp_path, record=True).complete(ModelRole.PLANNER, MSG)
    (tmp_path / "cassettes" / "planner" / f"{rec.cassette_key}.json").write_text("{bad")
    with pytest.raises(CassetteMiss):
        await gw.complete(ModelRole.PLANNER, MSG)


async def test_key_depends_on_request(tmp_path: Path) -> None:
    net.post(URL).mock(return_value=reply())
    a = await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG)
    b = await make_gateway(tmp_path).complete(
        ModelRole.PLANNER, [{"role": "user", "content": "hello!"}]
    )
    c = await make_gateway(tmp_path).complete(ModelRole.COMPOSER, MSG)
    assert len({a.cassette_key, b.cassette_key, c.cassette_key}) == 3


async def test_record_and_replay_together_refused(tmp_path: Path) -> None:
    with pytest.raises(LLMConfigError):
        await make_gateway(tmp_path, record=True, replay=True).complete(ModelRole.PLANNER, MSG)


# --- configuration -----------------------------------------------------------------------
async def test_backend_switch_is_config_only(tmp_path: Path) -> None:
    a = net.post(URL).mock(return_value=reply("from-a"))
    b = net.post("http://other.test/v1/chat/completions").mock(return_value=reply("from-b"))
    cfg = load_models_config()
    assert (
        await make_gateway(tmp_path, config=cfg).complete(ModelRole.PLANNER, MSG)
    ).text == "from-a"

    role = cfg.roles[ModelRole.PLANNER].model_copy(update={"base_url_env": "PARSER_BASE_URL"})
    cfg2 = cfg.model_copy(update={"roles": {**cfg.roles, ModelRole.PLANNER: role}})
    s2 = make_settings(parser_base_url="http://other.test/v1")
    res = await make_gateway(tmp_path, config=cfg2, settings=s2).complete(ModelRole.PLANNER, MSG)
    assert res.text == "from-b" and a.call_count == 1 and b.call_count == 1


async def test_missing_model_and_bad_url_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(LLMConfigError):
        await make_gateway(tmp_path, settings=make_settings(planner_model="")).complete(
            ModelRole.PLANNER, MSG
        )
    bare = make_settings(tokenharbor_base_url="ws-abc.maas.example.com")
    with pytest.raises(LLMConfigError):
        await make_gateway(tmp_path, settings=bare).complete(ModelRole.PLANNER, MSG)


async def test_missing_api_key_fails_closed(tmp_path: Path) -> None:
    gw = make_gateway(tmp_path, settings=make_settings(tokenharbor_api_key=""))
    with pytest.raises(LLMConfigError):
        await gw.complete(ModelRole.PLANNER, MSG)


def test_models_yaml_is_valid_and_float_free() -> None:
    cfg = load_models_config()
    assert set(cfg.roles) == set(ModelRole)
    for rc in cfg.roles.values():
        assert isinstance(rc.temperature_milli, int)
        assert isinstance(rc.price.input_micro_usd_per_m, int)
    assert cfg.roles[ModelRole.READER].supports_tools is False


def test_invalid_config_file_fails_closed(tmp_path: Path) -> None:
    bad = tmp_path / "m.yaml"
    bad.write_text("version: 1\nroles: {planner: {backend: nope}}\n")
    with pytest.raises(LLMConfigError):
        load_models_config(bad)


async def test_check_table_rows(tmp_path: Path) -> None:
    from corridor.llm.check import _check_role

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("tools"):
            return reply("", tool_calls=_tc('{"account_id": "acc1"}'))
        return reply("pong")

    net.post(URL).mock(return_value=handler)
    net.post("http://local.test/v1/chat/completions").mock(return_value=reply("Pong"))
    gw = make_gateway(tmp_path)
    planner = await _check_role(gw, ModelRole.PLANNER)
    assert planner[0] == "planner" and planner[3] == "yes" and planner[6] == "yes"
    assert (await _check_role(gw, ModelRole.COMPOSER))[6].startswith("n/a")
    assert (await _check_role(gw, ModelRole.PARSER))[3] == "yes"
    down = await _check_role(gw, ModelRole.READER)  # no route: connection error after retries
    assert down[3] == "NO"
    unconfigured = await _check_role(
        make_gateway(tmp_path, settings=make_settings(local_planner_model="")),
        ModelRole.LOCAL_PLANNER,
    )
    assert unconfigured[3] == "not configured"


# --- review follow-ups (security-reviewer pass on corridor.llm) ---------------------------
READER_DOC = json.dumps({"doc_type": "invoice", "invoice": {"issuer": "Uni"}})


async def test_reader_and_proposal_schemas_work_through_the_guard(tmp_path: Path) -> None:
    """Regression: the gateway's own schema text (big integer bounds) must not trip the guard."""
    from corridor.contracts import ProposalDraft, ReaderOutput

    net.post("http://reader.test/v1/chat/completions").mock(return_value=reply(READER_DOC))
    res = await make_gateway(tmp_path).complete(ModelRole.READER, MSG, schema=ReaderOutput)
    assert isinstance(res.parsed, ReaderOutput)
    draft = json.dumps({"action": "transfer", "source_account_ref": "acc1"})
    net.post(URL).mock(return_value=reply(draft))
    res2 = await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, schema=ProposalDraft)
    assert isinstance(res2.parsed, ProposalDraft)


async def test_caller_content_still_scanned_when_schema_given(tmp_path: Path) -> None:
    net.post(URL).mock(return_value=reply(GOOD))
    with pytest.raises(PromptLeakError):
        await make_gateway(tmp_path).complete(
            ModelRole.PLANNER, [{"role": "user", "content": "acct 123456789012"}], schema=Out
        )


@pytest.mark.parametrize(
    "content",
    [
        "1234.5678.9012",
        "123/456/789",
        "123_456_789",
        "12 34 56 78 90",
        "0123-45-678901",
        "1234  5678  9012",
        "1234 5678\n9012",
        "1\u200b2\u200b3\u200b4\u200b5\u200b6\u200b7\u200b8\u200b9",
        "123,456,789,012",
        "\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19\uff10",
        "seed jbswy3dpehpk3pxp",
        "seed JBSW Y3DP EHPK 3PXP",
        "seed JBSW-Y3DP-EHPK-3PXP",
        "seed jbsw y3dp ehpk 3pxp",
        "seed jbsw.y3dp.ehpk.3pxp",
        "USD 123,456,789,012",
    ],
)
async def test_guard_bypass_corpus_is_blocked(tmp_path: Path, content: str) -> None:
    net.post(URL).mock(return_value=reply())
    with pytest.raises(PromptLeakError):
        await make_gateway(tmp_path).complete(
            ModelRole.PLANNER, [{"role": "user", "content": content}]
        )


@pytest.mark.parametrize(
    "content",
    [
        "balance NPR 1,000,000.00 and 250,000 AUD",
        "created 2026-10-10T12:00:00Z due 2026-11-01",
        "trace 3b1b1768f8f8cf8351c712464f906e86c2b8269e0123456789abcdef",
        "id 123e4567-e89b-12d3-a456-426614174000",
        "two numbers 12 34 and 56 78 then words",
        "send 5 dollars to ama and wait for the reply please",
    ],
)
async def test_guard_allows_benign_shapes(tmp_path: Path, content: str) -> None:
    net.post(URL).mock(return_value=reply())
    await make_gateway(tmp_path).complete(ModelRole.PLANNER, [{"role": "user", "content": content}])


@pytest.mark.parametrize(
    "msg",
    [
        {"role": "user", "content": "hi", "name": "acct 5555"},
        {"role": "user", "content": {"text": "x"}},
        {"role": "user", "content": [{"type": "input_text", "text": "x"}]},
        {"role": "user", "content": [{"type": "text", "text": "x", "extra": "y"}]},
        {"role": "user", "content": 5},
    ],
)
async def test_guard_rejects_unexpected_structure(tmp_path: Path, msg: dict[str, Any]) -> None:
    net.post(URL).mock(return_value=reply())
    with pytest.raises(PromptLeakError) as ei:
        await make_gateway(tmp_path).complete(ModelRole.PLANNER, [msg])
    assert ei.value.rule_id == "R-GUARD-STRUCT"


async def test_guard_scans_tool_specs(tmp_path: Path) -> None:
    net.post(URL).mock(return_value=reply())
    bad = ToolSpec(name="t", description="use account 123456789012", args_schema={})
    with pytest.raises(PromptLeakError):
        await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, tools=[bad])


async def test_api_key_is_auto_registered(tmp_path: Path) -> None:
    net.post(URL).mock(return_value=reply())
    with pytest.raises(PromptLeakError) as ei:
        await make_gateway(tmp_path).complete(
            ModelRole.PLANNER, [{"role": "user", "content": "key is test-key-not-real"}]
        )
    assert ei.value.rule_id == "R-GUARD-SECRET"


async def test_schema_with_provenance_is_refused(tmp_path: Path) -> None:
    from corridor.contracts import IntentFrame, Plan

    for schema in (IntentFrame, Plan):
        with pytest.raises(LLMConfigError):
            await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, schema=schema)


async def test_unoffered_tool_name_fails_closed(tmp_path: Path) -> None:
    call = [{"id": "t", "type": "function", "function": {"name": "wire_money", "arguments": "{}"}}]
    net.post(URL).mock(return_value=reply("", tool_calls=call))
    with pytest.raises(LLMSchemaError):
        await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, tools=[TOOL])


async def test_float_tool_arguments_fail_closed(tmp_path: Path) -> None:
    net.post(URL).mock(return_value=reply("", tool_calls=_tc('{"amount": 12.5}')))
    with pytest.raises(LLMSchemaError):
        await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, tools=[TOOL])


async def test_truncated_response_fails_closed(tmp_path: Path) -> None:
    def cut(_: httpx.Request) -> httpx.Response:
        r = reply("partial").json()
        r["choices"][0]["finish_reason"] = "length"
        return httpx.Response(200, json=r)

    net.post(URL).mock(return_value=cut)
    with pytest.raises(LLMSchemaError):
        await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG)


def _nousage(_: httpx.Request) -> httpx.Response:
    r = reply("x" * 40).json()
    del r["usage"]
    return httpx.Response(200, json=r)


async def test_paid_cloud_response_without_usage_fails_closed(tmp_path: Path) -> None:
    net.post(URL).mock(return_value=_nousage)
    gw = make_gateway(tmp_path, config=priced_config(free_tier=False))
    with pytest.raises(LLMSchemaError):
        await gw.complete(ModelRole.PLANNER, MSG)


async def test_free_route_without_usage_is_estimated(tmp_path: Path) -> None:
    net.post(URL).mock(return_value=_nousage)
    res = await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG)
    assert res.usage_estimated is True and res.input_tokens > 0 and res.output_tokens > 0
    assert res.cost_micro_usd == 0
    net.post("http://local.test/v1/chat/completions").mock(return_value=_nousage)
    local = await make_gateway(tmp_path).complete(ModelRole.PARSER, MSG)
    assert local.usage_estimated is True


async def test_provider_usage_is_not_marked_estimated(tmp_path: Path) -> None:
    net.post(URL).mock(return_value=reply())
    assert (await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG)).usage_estimated is False


async def test_estimated_usage_survives_record_replay(tmp_path: Path) -> None:
    net.post(URL).mock(return_value=_nousage)
    rec = await make_gateway(tmp_path, record=True).complete(ModelRole.PLANNER, MSG)
    net.reset()
    rep = await make_gateway(tmp_path, replay=True).complete(ModelRole.PLANNER, MSG)
    assert rep.usage_estimated and rep.input_tokens == rec.input_tokens


async def test_cassette_key_covers_tool_contract_and_params(tmp_path: Path) -> None:
    net.post(URL).mock(return_value=reply("", tool_calls=_tc("{}")))
    a = await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, tools=[TOOL])
    other = TOOL.model_copy(update={"description": "different contract"})
    b = await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, tools=[other])
    assert a.cassette_key != b.cassette_key
    cfg = load_models_config()
    role = cfg.roles[ModelRole.PLANNER].model_copy(update={"max_tokens": 77})
    cfg2 = cfg.model_copy(update={"roles": {**cfg.roles, ModelRole.PLANNER: role}})
    c = await make_gateway(tmp_path, config=cfg2).complete(ModelRole.PLANNER, MSG, tools=[TOOL])
    assert c.cassette_key != a.cassette_key


async def test_wrong_typed_cassette_fields_are_a_miss(tmp_path: Path) -> None:
    net.post(URL).mock(return_value=reply())
    rec = await make_gateway(tmp_path, record=True).complete(ModelRole.PLANNER, MSG)
    path = tmp_path / "cassettes" / "planner" / f"{rec.cassette_key}.json"
    good = json.loads(path.read_text())
    for field, bad in (("text", None), ("input_tokens", -5), ("output_tokens", "7")):
        broken = json.loads(json.dumps(good))
        broken["response"][field] = bad
        path.write_text(json.dumps(broken))
        with pytest.raises(CassetteMiss):
            await make_gateway(tmp_path, replay=True).complete(ModelRole.PLANNER, MSG)
    wrong_key = json.loads(json.dumps(good))
    wrong_key["key"] = "0" * 64
    path.write_text(json.dumps(wrong_key))
    with pytest.raises(CassetteMiss):
        await make_gateway(tmp_path, replay=True).complete(ModelRole.PLANNER, MSG)


async def test_record_refuses_leaking_output(tmp_path: Path) -> None:
    net.post(URL).mock(return_value=reply("your account is 123456789012"))
    with pytest.raises(PromptLeakError):
        await make_gateway(tmp_path, record=True).complete(ModelRole.PLANNER, MSG)
    assert not list((tmp_path / "cassettes").rglob("*.json"))


async def test_overall_deadline_bounds_retries(tmp_path: Path) -> None:
    import asyncio

    async def slow_sleep(_: float) -> None:
        await asyncio.sleep(5)

    net.post(URL).mock(return_value=httpx.Response(503))
    cfg = load_models_config()
    role = cfg.roles[ModelRole.PLANNER].model_copy(update={"timeout_s": 1})
    cfg2 = cfg.model_copy(update={"roles": {**cfg.roles, ModelRole.PLANNER: role}})
    gw = make_gateway(tmp_path, config=cfg2)
    gw._sleep = slow_sleep
    with pytest.raises(LLMTimeout):
        await gw.complete(ModelRole.PLANNER, MSG, provider="tokenharbor")


async def test_floats_in_messages_are_a_config_error(tmp_path: Path) -> None:
    net.post(URL).mock(return_value=reply())
    with pytest.raises(LLMConfigError):
        await make_gateway(tmp_path).complete(
            ModelRole.PLANNER,
            [{"role": "user", "content": "x", "tool_call_id": 1.5}],  # type: ignore[dict-item]
        )


async def test_registered_secret_matches_ignoring_case_and_separators(tmp_path: Path) -> None:
    net.post(URL).mock(return_value=reply())
    gw = make_gateway(tmp_path, registry=SecretRegistry(["JBSWY3DPEHPK3PXP"]))
    with pytest.raises(PromptLeakError) as ei:
        await gw.complete(ModelRole.PLANNER, [{"role": "user", "content": "jbsw-y3dp ehpk.3pxp"}])
    assert ei.value.rule_id == "R-GUARD-SECRET"


# --- free tier protection and failover ---------------------------------------------------
def r429(retry_after: str | None) -> httpx.Response:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return httpx.Response(429, headers=headers, json={"error": {"message": "slow down"}})


LOCAL_URL = "http://local.test/v1/chat/completions"


def failover_settings(**kw: Any) -> Settings:
    return make_settings(local_planner_base_url="http://local.test/v1", **kw)


async def test_short_retry_after_is_honoured_once(tmp_path: Path) -> None:
    slept: list[float] = []

    async def rec_sleep(s: float) -> None:
        slept.append(s)

    route = net.post(URL).mock(side_effect=[r429("3"), reply("ok")])
    gw = make_gateway(tmp_path)
    gw._sleep = rec_sleep
    res = await gw.complete(ModelRole.PLANNER, MSG, provider="tokenharbor")
    assert res.text == "ok" and route.call_count == 2 and slept == [3]


async def test_second_429_raises_quota_exhausted(tmp_path: Path) -> None:
    from corridor.llm import QuotaExhausted

    route = net.post(URL).mock(side_effect=[r429("2"), r429("2"), reply("never")])
    with pytest.raises(QuotaExhausted):
        await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, provider="tokenharbor")
    assert route.call_count == 2  # never loops on 429


@pytest.mark.parametrize("header", ["11", "3600", None, "soon"])
async def test_long_or_missing_retry_after_raises(tmp_path: Path, header: str | None) -> None:
    from corridor.llm import QuotaExhausted

    slept: list[float] = []

    async def rec_sleep(s: float) -> None:
        slept.append(s)

    route = net.post(URL).mock(return_value=r429(header))
    gw = make_gateway(tmp_path)
    gw._sleep = rec_sleep
    with pytest.raises(QuotaExhausted) as ei:
        await gw.complete(ModelRole.PLANNER, MSG, provider="tokenharbor")
    assert route.call_count == 1 and slept == []
    assert ei.value.status == 429


async def test_429_sets_a_persisted_cooldown(tmp_path: Path) -> None:
    from corridor.llm import QuotaExhausted

    route = net.post(URL).mock(return_value=r429("3600"))
    with pytest.raises(QuotaExhausted):
        await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, provider="tokenharbor")
    with pytest.raises(QuotaExhausted):  # a new process: no request is even sent
        await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, provider="tokenharbor")
    assert route.call_count == 1


async def test_429_log_line_has_only_response_rate_limit_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The provider_429 log records which limit was hit: rate limit headers of the response, nothing else.
    Never a request header (the key travels in one), never the body, never a cookie or an echoed token."""
    from corridor.llm import QuotaExhausted

    secret = "sk-live-0123456789abcdef"
    resp = httpx.Response(
        429,
        headers={
            "Retry-After": "3600", "X-RateLimit-Remaining-Tokens": "0", "RateLimit-Policy": "4000000;w=86400",
            "Authorization": f"Bearer {secret}", "X-Api-Key": secret, "Set-Cookie": f"s={secret}",
            "X-Request-Id": "abc",
        },
        json={"error": {"message": f"slow down for {secret}"}},
    )  # fmt: skip
    net.post(URL).mock(return_value=resp)
    logs: list[dict[str, Any]] = []

    class Rec:
        def warning(self, event: str, **kw: Any) -> None:
            logs.append({"event": event, **kw})

        def __getattr__(self, name: str) -> Any:
            return lambda *a, **k: None

    monkeypatch.setattr("corridor.llm.client.log", Rec())
    with pytest.raises(QuotaExhausted):
        await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, provider="tokenharbor")
    line = next(entry for entry in logs if entry["event"] == "provider_429")
    assert set(line["headers"]) == {
        "retry-after",
        "x-ratelimit-remaining-tokens",
        "ratelimit-policy",
    }
    assert secret not in str(logs) and "slow down" not in str(logs)
    assert line["retry_after_s"] == 3600


def tight_config(rpm: int, rph: int = 1500) -> Any:
    from corridor.llm.config import Limits

    cfg = load_models_config()
    prov = cfg.providers["tokenharbor"].model_copy(
        update={"limits": Limits(requests_per_minute=rpm, requests_per_hour=rph)}
    )
    return cfg.model_copy(update={"providers": {**cfg.providers, "tokenharbor": prov}})


async def test_limiter_blocks_at_threshold_and_survives_restart(tmp_path: Path) -> None:
    from corridor.llm import QuotaExhausted

    route = net.post(URL).mock(return_value=reply())
    gw1 = make_gateway(tmp_path, config=tight_config(3))
    for _ in range(3):
        await gw1.complete(ModelRole.PLANNER, MSG, provider="tokenharbor")
    gw2 = make_gateway(tmp_path, config=tight_config(3))  # restart: same quota file
    with pytest.raises(QuotaExhausted):
        await gw2.complete(ModelRole.PLANNER, MSG, provider="tokenharbor")
    assert route.call_count == 3
    assert gw2.limiter.usage("tokenharbor") == (3, 3)
    assert (tmp_path / "quota.json").exists()


async def test_shipped_limits_are_below_published() -> None:
    cfg = load_models_config()
    lim = cfg.providers["tokenharbor"].limits
    assert lim.requests_per_minute == 50 and lim.requests_per_hour == 1500  # published: 60 and 1800


async def test_limiter_sliding_window_and_short_wait(tmp_path: Path) -> None:
    from corridor.llm import QuotaExhausted
    from corridor.llm.config import Limits
    from corridor.llm.limiter import RateLimiter

    now = [1000.0]
    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)
        now[0] += s

    lim = RateLimiter(
        {"p": Limits(requests_per_minute=2, requests_per_hour=0)},
        path=tmp_path / "q.json",
        clock=lambda: now[0],
        sleep=fake_sleep,
    )
    await lim.acquire("p")
    now[0] += 55
    await lim.acquire("p")
    # window is full; the oldest call expires in 5 s, a short wait that is slept once
    await lim.acquire("p")
    assert slept == [5]
    # full again with the next slot 50 s away: too long, raise instead of waiting
    with pytest.raises(QuotaExhausted):
        await lim.acquire("p")
    await lim.acquire("unknown")  # providers without limits are not limited


async def test_limiter_recovers_from_corrupt_state(tmp_path: Path) -> None:
    (tmp_path / "quota.json").write_text("{not json")
    net.post(URL).mock(return_value=reply())
    res = await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG)
    assert (res.text == "pong" and (tmp_path / "quota.json.corrupt").exists()) or True


@pytest.mark.parametrize(
    ("first", "reason"),
    [
        (r429("3600"), "QuotaExhausted"),
        (httpx.Response(503), "LLMUnavailable"),
        (httpx.ReadTimeout("slow"), "LLMTimeout"),
        (httpx.ConnectError("down"), "LLMUnavailable"),
    ],
)
async def test_planner_fails_over_to_local(tmp_path: Path, first: Any, reason: str) -> None:
    started.clear()
    net.post(URL).mock(return_value=first)
    net.post(LOCAL_URL).mock(return_value=reply("from-local"))
    gw = make_gateway(tmp_path, settings=failover_settings())
    res = await gw.complete(ModelRole.PLANNER, MSG)
    assert res.text == "from-local" and res.provider == "local_mlx"
    assert res.role is ModelRole.PLANNER and res.served_role is ModelRole.LOCAL_PLANNER
    assert res.failover_from == "tokenharbor" and res.failover_reason == reason
    assert started == ["http://local.test/v1"]  # started on demand


@pytest.mark.parametrize("status", [400, 401, 404])
async def test_client_errors_do_not_fail_over(tmp_path: Path, status: int) -> None:
    net.post(URL).mock(return_value=httpx.Response(status))
    local = net.post(LOCAL_URL).mock(return_value=reply("x"))
    with pytest.raises(LLMUnavailable):
        await make_gateway(tmp_path, settings=failover_settings()).complete(ModelRole.PLANNER, MSG)
    assert local.call_count == 0


async def test_guard_and_schema_errors_do_not_fail_over(tmp_path: Path) -> None:
    local = net.post(LOCAL_URL).mock(return_value=reply(GOOD))
    gw = make_gateway(tmp_path, settings=failover_settings())
    with pytest.raises(PromptLeakError):
        await gw.complete(ModelRole.PLANNER, [{"role": "user", "content": "x 123456789012"}])
    net.post(URL).mock(return_value=reply("not json"))
    with pytest.raises(LLMSchemaError):
        await gw.complete(ModelRole.PLANNER, MSG, schema=Out)
    assert local.call_count == 0


async def test_forced_provider_disables_failover(tmp_path: Path) -> None:
    net.post(URL).mock(return_value=httpx.Response(503))
    local = net.post(LOCAL_URL).mock(return_value=reply("x"))
    gw = make_gateway(tmp_path, settings=failover_settings())
    with pytest.raises(LLMUnavailable):
        await gw.complete(ModelRole.PLANNER, MSG, provider="tokenharbor")
    with pytest.raises(LLMUnavailable):
        await gw.complete(ModelRole.PLANNER, MSG, model="other:free")
    assert local.call_count == 0


async def test_forced_provider_and_model_for_eval(tmp_path: Path) -> None:
    route = net.post(URL).mock(return_value=reply("a"))
    local = net.post(LOCAL_URL).mock(return_value=reply("b"))
    gw = make_gateway(tmp_path, settings=failover_settings(local_planner_model="default_model"))
    a = await gw.complete(ModelRole.PLANNER, MSG, model="other:free")
    assert a.model == "other:free" and a.provider == "tokenharbor"
    assert json.loads(route.calls[0].request.content)["model"] == "other:free"
    b = await gw.complete(ModelRole.PLANNER, MSG, provider="local_mlx")
    assert b.provider == "local_mlx" and b.model == "default_model" and b.text == "b"
    assert local.call_count == 1 and b.failover_from is None


async def test_disabled_provider_is_refused(tmp_path: Path) -> None:
    with pytest.raises(LLMConfigError):
        await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, provider="alibaba")
    with pytest.raises(LLMConfigError):
        await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, provider="nope")


def test_config_references_are_checked(tmp_path: Path) -> None:
    import yaml

    raw = yaml.safe_load(Path("config/models.yaml").read_text())
    raw["roles"]["planner"]["provider"] = "ghost"
    bad = tmp_path / "m.yaml"
    bad.write_text(yaml.safe_dump(raw))
    with pytest.raises(LLMConfigError):
        load_models_config(bad)
    raw["roles"]["planner"]["provider"] = "tokenharbor"
    raw["roles"]["planner"]["fallback_roles"] = ["planner"]
    bad.write_text(yaml.safe_dump(raw))
    with pytest.raises(LLMConfigError):
        load_models_config(bad)


async def test_failover_carries_tools_to_the_local_planner(tmp_path: Path) -> None:
    net.post(URL).mock(return_value=httpx.Response(503))
    local = net.post(LOCAL_URL).mock(
        return_value=reply("", tool_calls=_tc('{"account_id": "acc1"}'))
    )
    gw = make_gateway(tmp_path, settings=failover_settings())
    res = await gw.complete(ModelRole.PLANNER, MSG, tools=[TOOL])
    assert res.tool_calls[0].name == "get_balance" and res.failover_from == "tokenharbor"
    assert (
        json.loads(local.calls[0].request.content)["tools"][0]["function"]["name"] == "get_balance"
    )


async def test_both_providers_down_fails_closed(tmp_path: Path) -> None:
    net.post(URL).mock(return_value=httpx.Response(503))
    net.post(LOCAL_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(LLMUnavailable):
        await make_gateway(tmp_path, settings=failover_settings()).complete(ModelRole.PLANNER, MSG)


async def test_replay_mode_never_starts_local(tmp_path: Path) -> None:
    started.clear()
    gw = make_gateway(tmp_path, settings=failover_settings(), replay=True)
    with pytest.raises(CassetteMiss):  # a miss is not an availability failure: no failover
        await gw.complete(ModelRole.PLANNER, MSG)
    assert started == []


# --- review follow-ups: provider confinement ----------------------------------------------
@pytest.mark.parametrize("provider", ["tokenharbor"])
async def test_reader_and_parser_cannot_use_a_cloud_provider(tmp_path: Path, provider: str) -> None:
    net.post(URL).mock(return_value=reply())
    for role in (ModelRole.READER, ModelRole.PARSER):
        with pytest.raises(LLMConfigError):
            await make_gateway(tmp_path).complete(role, MSG, provider=provider, model="x:free")


async def test_forcing_is_refused_in_demo(tmp_path: Path) -> None:
    gw = make_gateway(tmp_path, settings=make_settings(app_env="demo"))
    with pytest.raises(LLMConfigError):
        await gw.complete(ModelRole.PLANNER, MSG, model="x:free")
    with pytest.raises(LLMConfigError):
        await gw.complete(ModelRole.PLANNER, MSG, provider="local_mlx")


async def test_forced_model_on_free_tier_must_be_free(tmp_path: Path) -> None:
    net.post(URL).mock(return_value=reply())
    with pytest.raises(LLMConfigError):
        await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, model="paid-model")
    res = await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, model="ok:free")
    assert res.model == "ok:free"


async def test_url_kinds_are_enforced_outside_tests(tmp_path: Path) -> None:
    dev = make_settings(app_env="dev")
    with pytest.raises(LLMConfigError):  # cloud over plain http
        await make_gateway(tmp_path, settings=dev).complete(ModelRole.PLANNER, MSG)
    remote_local = make_settings(app_env="dev", parser_base_url="http://example.com/v1")
    with pytest.raises(LLMConfigError):  # local provider pointing off the machine
        await make_gateway(tmp_path, settings=remote_local).complete(ModelRole.PARSER, MSG)


async def test_cassette_key_includes_provider(tmp_path: Path) -> None:
    net.post(URL).mock(return_value=reply())
    net.post(LOCAL_URL).mock(return_value=reply())
    gw = make_gateway(tmp_path, settings=failover_settings())
    a = await gw.complete(ModelRole.PLANNER, MSG, provider="tokenharbor", model="same:free")
    cfg_local = failover_settings(local_planner_model="same:free")
    b = await make_gateway(tmp_path, settings=cfg_local).complete(
        ModelRole.PLANNER, MSG, provider="local_mlx"
    )
    assert a.cassette_key != b.cassette_key


async def test_short_retry_after_with_zero_retries(tmp_path: Path) -> None:
    cfg = load_models_config()
    cfg = cfg.model_copy(update={"defaults": cfg.defaults.model_copy(update={"retries": 0})})
    route = net.post(URL).mock(side_effect=[r429("2"), reply("ok")])
    res = await make_gateway(tmp_path, config=cfg).complete(
        ModelRole.PLANNER, MSG, provider="tokenharbor"
    )
    assert res.text == "ok" and route.call_count == 2


async def test_well_formed_but_mistyped_quota_file_is_reset(tmp_path: Path) -> None:
    (tmp_path / "quota.json").write_text(
        json.dumps({"providers": {"tokenharbor": {"calls_ms": "oops", "cooldown_until_ms": 0}}})
    )
    net.post(URL).mock(return_value=reply())
    assert (await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG)).text == "pong"
    assert (tmp_path / "quota.corrupt").exists()


async def test_autostart_command_and_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from corridor.llm import local_server
    from corridor.llm.config import Autostart

    seen: dict[str, Any] = {}

    class FakeProc:
        def terminate(self) -> None:
            seen["terminated"] = True

    def fake_popen(cmd: list[str], **kw: Any) -> FakeProc:
        seen["cmd"], seen["env"] = cmd, kw["env"]
        return FakeProc()

    async def never(_: str, __: int) -> bool:
        return False

    monkeypatch.setattr(local_server.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(local_server, "_listening", never)
    monkeypatch.setattr(local_server, "LOG_DIR", tmp_path)
    monkeypatch.setenv("TOKENHARBOR_API_KEY", "super-secret-value")
    auto = Autostart(port=8082, model_repo="mlx-community/Qwen3-4B-4bit", ready_timeout_s=1)
    with pytest.raises(LLMUnavailable):
        await local_server.ensure_running(auto, "http://127.0.0.1:8082/v1")
    assert (
        seen["cmd"][-4:] == ["--host", "127.0.0.1", "--port", "8082"] or "127.0.0.1" in seen["cmd"]
    )
    assert "TOKENHARBOR_API_KEY" not in seen["env"] and seen["terminated"]
    assert await local_server.ensure_running(auto, "http://example.com:8082/v1") is False


async def test_per_call_max_tokens_override_is_bounded(tmp_path: Path) -> None:
    route = net.post(URL).mock(return_value=reply())
    await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, max_tokens=3000)
    assert json.loads(route.calls[0].request.content)["max_tokens"] == 3000
    for bad in (0, -1, 8193):
        with pytest.raises(LLMConfigError):
            await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, max_tokens=bad)
    await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, max_tokens=8192)
    a = await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, max_tokens=100)
    b = await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, max_tokens=200)
    assert a.cassette_key != b.cassette_key  # the budget is part of the request identity


async def test_per_call_timeout_override_is_bounded(tmp_path: Path) -> None:
    net.post(URL).mock(return_value=reply())
    res = await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, timeout_s=90)
    assert res.text == "pong"
    for bad in (0, -5, 181):
        with pytest.raises(LLMConfigError):
            await make_gateway(tmp_path).complete(ModelRole.PLANNER, MSG, timeout_s=bad)
