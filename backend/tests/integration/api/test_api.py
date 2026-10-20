"""HTTP surface: auth, SSE order, ownership, limits, CORS, dev page."""

import json
from datetime import date

import httpx
from sqlalchemy import Engine

from agent_helpers import TOKENS
from corridor.agent.service import AgentService
from corridor.api.app import MAX_BODY_BYTES, create_app
from corridor.api.auth import TokenAuth
from corridor.config import AppEnv, Settings
from corridor.confirm import current_code


def hdr(user: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[user]}"}


def parse_sse(body: str) -> list[dict]:
    out = []
    for block in body.strip().split("\n\n"):
        data = next((ln[6:] for ln in block.splitlines() if ln.startswith("data: ")), None)
        if data:
            out.append(json.loads(data))
    return out


async def test_requires_a_valid_token(client: httpx.AsyncClient) -> None:
    for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic abc"}):
        assert (await client.get("/accounts", headers=headers)).status_code == 401
        r = await client.post("/chat", json={"message": "hi"}, headers=headers)
        assert r.status_code == 401


async def test_user_comes_only_from_the_token(client: httpx.AsyncClient) -> None:
    r = await client.get("/accounts", headers=hdr("usr_ramesh"))
    ids = {a["account_id"] for a in r.json()["accounts"]}
    assert ids == {"acc_ramesh_qar", "acc_ramesh_npr"}
    spoof = await client.get(
        "/accounts?user_id=usr_priya", headers={**hdr("usr_ramesh"), "X-User-Id": "usr_priya"}
    )
    assert {a["account_id"] for a in spoof.json()["accounts"]} == ids


async def test_body_cannot_name_a_user(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/chat", json={"message": "hi", "user_id": "usr_priya"}, headers=hdr("usr_ramesh")
    )
    assert r.status_code == 422


async def test_get_endpoints(client: httpx.AsyncClient) -> None:
    for path, key in (
        ("/accounts", "accounts"),
        ("/payees", "payees"),
        ("/transfers", "transfers"),
    ):
        r = await client.get(path, headers=hdr("usr_priya"))
        assert r.status_code == 200 and key in r.json()
    payees = (await client.get("/payees", headers=hdr("usr_priya"))).json()["payees"]
    assert all(p["payee_id"].startswith("pay_priya") for p in payees)


async def test_chat_streams_ordered_events_and_confirm_executes(
    client: httpx.AsyncClient, service: AgentService
) -> None:
    r = await client.post(
        "/chat", json={"message": "Ama lai 40 hajar pathau"}, headers=hdr("usr_ramesh")
    )
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    thread = r.headers["x-thread-id"]
    events = parse_sse(r.text)
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
    assert events[0]["type"] == "intent" and events[-1]["type"] == "confirm_request"
    h = events[-1]["data"]["proposal_hash"]
    bad = await client.post(
        f"/chat/{thread}/confirm", json={"proposal_hash": "0" * 64}, headers=hdr("usr_ramesh")
    )
    assert "hash_mismatch" in bad.text and "executed" not in bad.text
    again = await client.post(
        "/chat",
        json={"message": "Ama lai 40 hajar pathau", "thread_id": thread},
        headers=hdr("usr_ramesh"),
    )
    h = parse_sse(again.text)[-1]["data"]["proposal_hash"]
    assert parse_sse(again.text)[-1]["data"]["required_level"] == "mfa"  # NPR 20,000 is above low
    nocode = await client.post(
        f"/chat/{thread}/confirm", json={"proposal_hash": h}, headers=hdr("usr_ramesh")
    )
    assert "mfa_required" in nocode.text and "executed" not in nocode.text
    code = current_code("usr_ramesh", service.deps.now_fn(), service.deps.secrets_dir)
    ok = await client.post(
        f"/chat/{thread}/confirm",
        json={"proposal_hash": h, "totp": code},
        headers=hdr("usr_ramesh"),
    )
    assert "executed" in [e["type"] for e in parse_sse(ok.text)]
    again = await client.post(
        f"/chat/{thread}/confirm", json={"proposal_hash": h}, headers=hdr("usr_ramesh")
    )
    assert again.status_code == 409


async def test_other_users_thread_is_404(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/chat", json={"message": "Ama lai 40 hajar pathau"}, headers=hdr("usr_ramesh")
    )
    thread = r.headers["x-thread-id"]
    h = parse_sse(r.text)[-1]["data"]["proposal_hash"]
    for call in (
        client.post(f"/chat/{thread}/confirm", json={"proposal_hash": h}, headers=hdr("usr_priya")),
        client.post(f"/chat/{thread}/cancel", headers=hdr("usr_priya")),
        client.post("/chat", json={"message": "hi", "thread_id": thread}, headers=hdr("usr_priya")),
    ):
        assert (await call).status_code == 404


async def test_cancel(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/chat", json={"message": "Ama lai 40 hajar pathau"}, headers=hdr("usr_ramesh")
    )
    thread = r.headers["x-thread-id"]
    c = await client.post(f"/chat/{thread}/cancel", headers=hdr("usr_ramesh"))
    assert c.status_code == 200 and "executed" not in c.text


async def test_oversized_body_is_413(client: httpx.AsyncClient) -> None:
    big = json.dumps({"message": "x" * (MAX_BODY_BYTES + 10)})
    r = await client.post(
        "/chat", content=big, headers={**hdr("usr_priya"), "Content-Type": "application/json"}
    )
    assert r.status_code == 413


async def test_message_length_is_limited(client: httpx.AsyncClient) -> None:
    r = await client.post("/chat", json={"message": "x" * 3000}, headers=hdr("usr_priya"))
    assert r.status_code == 422


async def test_cors_allows_only_the_frontend_origin(client: httpx.AsyncClient) -> None:
    ok = await client.get("/healthz", headers={"Origin": "http://localhost:3000"})
    bad = await client.get("/healthz", headers={"Origin": "http://evil.example"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert "access-control-allow-origin" not in bad.headers


async def test_dev_chat_is_dev_and_loopback_only(
    service: AgentService, sandbox: Engine, client: httpx.AsyncClient
) -> None:
    page = await client.get("/dev/chat")
    assert page.status_code == 200 and TOKENS["usr_priya"] in page.text and "SANDBOX" in page.text
    prod = create_app(
        settings=Settings(app_env=AppEnv.DEMO), service=service, engine=sandbox,
        auth=TokenAuth(TOKENS), today=lambda: date(2026, 10, 10),
    )  # fmt: skip
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=prod, client=("127.0.0.1", 1)),
        base_url="http://127.0.0.1",
    ) as c:
        assert (await c.get("/dev/chat")).status_code == 404
    remote = httpx.ASGITransport(app=client._transport.app, client=("203.0.113.9", 1))  # type: ignore[attr-defined]
    async with httpx.AsyncClient(transport=remote, base_url="http://127.0.0.1") as c:
        assert (await c.get("/dev/chat")).status_code == 404


async def test_rate_limit_returns_429(client: httpx.AsyncClient) -> None:
    codes = [
        (
            await client.post(
                "/chat", json={"message": "how much money do I have"}, headers=hdr("usr_anil")
            )
        ).status_code
        for _ in range(32)
    ]
    assert 429 in codes


async def test_ledger_endpoints_show_only_the_callers_entries(
    client: httpx.AsyncClient, service: AgentService
) -> None:
    await client.post(
        "/chat", json={"message": "send QAR 50 to my landlord"}, headers=hdr("usr_ramesh")
    )
    await client.post(
        "/chat", json={"message": "send AUD 10 to my landlord"}, headers=hdr("usr_priya")
    )
    mine = (await client.get("/ledger", headers=hdr("usr_ramesh"))).json()["entries"]
    assert mine and all(e["payload"]["user_id"] == "usr_ramesh" for e in mine)
    assert mine[0]["seq"] > mine[-1]["seq"]  # newest first
    verify = (await client.get("/ledger/verify", headers=hdr("usr_anil"))).json()
    assert verify["valid"] and verify["entries_checked"] >= len(mine)
    seq = mine[-1]["seq"]
    proof = (await client.get(f"/ledger/proof/{seq}", headers=hdr("usr_ramesh"))).json()
    assert proof["valid"] and proof["seq"] == seq
    other = await client.get(f"/ledger/proof/{seq}", headers=hdr("usr_priya"))
    assert other.status_code == 404  # someone else's entry is invisible
    assert (await client.get("/ledger/proof/99999", headers=hdr("usr_ramesh"))).status_code == 404
    assert (await client.get("/ledger")).status_code == 401


async def test_wrong_code_keeps_the_proposal_pending_and_five_lock(
    client: httpx.AsyncClient, service: AgentService
) -> None:
    r = await client.post(
        "/chat", json={"message": "Ama lai 40 hajar pathau"}, headers=hdr("usr_ramesh")
    )
    thread, h = r.headers["x-thread-id"], parse_sse(r.text)[-1]["data"]["proposal_hash"]
    for left in (4, 3, 2, 1):
        bad = await client.post(
            f"/chat/{thread}/confirm",
            json={"proposal_hash": h, "totp": "000001"},
            headers=hdr("usr_ramesh"),
        )
        rej = next(e for e in parse_sse(bad.text) if e["type"] == "rejected")
        assert rej["data"]["attempts_left"] == left
    last = await client.post(
        f"/chat/{thread}/confirm",
        json={"proposal_hash": h, "totp": "000001"},
        headers=hdr("usr_ramesh"),
    )
    assert (
        next(e for e in parse_sse(last.text) if e["type"] == "rejected")["data"]["locked"] is True
    )
    bad_unlock = await client.post("/unlock", json={"totp": "000002"}, headers=hdr("usr_ramesh"))
    assert bad_unlock.json()["unlocked"] is False
    code = current_code("usr_ramesh", service.deps.now_fn(), service.deps.secrets_dir)
    cooling = await client.post("/unlock", json={"totp": code}, headers=hdr("usr_ramesh"))
    assert cooling.json() == {"unlocked": False, "reason": "unlock_cooldown"}
    from datetime import timedelta

    later = service.deps.now_fn() + timedelta(minutes=11)
    service.deps.now_fn = lambda: later  # type: ignore[method-assign]
    code = current_code("usr_ramesh", later, service.deps.secrets_dir)
    good = await client.post("/unlock", json={"totp": code}, headers=hdr("usr_ramesh"))
    assert good.json()["unlocked"] is True
    assert (await client.post("/unlock", json={}, headers=hdr("usr_ramesh"))).status_code == 422


async def test_dev_totp_helper_is_dev_loopback_and_own_persona_only(
    service: AgentService, sandbox, client: httpx.AsyncClient
) -> None:
    r = await client.get("/dev/totp", headers=hdr("usr_priya"))
    assert (
        r.status_code == 200 and r.json()["user_id"] == "usr_priya" and len(r.json()["code"]) == 6
    )
    assert (await client.get("/dev/totp")).status_code == 401
    assert (
        await client.get("/dev/totp", headers={**hdr("usr_priya"), "Host": "evil.example"})
    ).status_code == 404
    from datetime import date

    for env in (AppEnv.DEMO, AppEnv.TEST):
        app = create_app(
            settings=Settings(app_env=env), service=service, engine=sandbox,
            auth=TokenAuth(TOKENS), today=lambda: date(2026, 10, 10),
        )  # fmt: skip
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
            assert (await c.get("/dev/totp", headers=hdr("usr_priya"))).status_code == 404
    remote = httpx.ASGITransport(app=client._transport.app, client=("203.0.113.9", 1))  # type: ignore[attr-defined]
    async with httpx.AsyncClient(transport=remote, base_url="http://127.0.0.1") as c:
        assert (await c.get("/dev/totp", headers=hdr("usr_priya"))).status_code == 404


async def test_demo_authenticator_exists_only_in_dev_and_demo_on_loopback_for_the_own_persona(
    service: AgentService, sandbox, client: httpx.AsyncClient
) -> None:
    from datetime import date

    r = await client.get(
        "/demo/authenticator", headers=hdr("usr_priya")
    )  # the test app runs as dev
    assert (
        r.status_code == 200
        and len(r.json()["code"]) == 6
        and 1 <= r.json()["valid_for_seconds"] <= 30
    )
    assert (await client.get("/demo/authenticator")).status_code == 401
    for env, status in ((AppEnv.DEMO, 200), (AppEnv.TEST, 404), (AppEnv.EVAL, 404)):
        app = create_app(
            settings=Settings(app_env=env), service=service, engine=sandbox,
            auth=TokenAuth(TOKENS), today=lambda: date(2026, 10, 10),
        )  # fmt: skip
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
            assert (
                await c.get("/demo/authenticator", headers=hdr("usr_priya"))
            ).status_code == status
    remote = httpx.ASGITransport(app=client._transport.app, client=("203.0.113.9", 1))  # type: ignore[attr-defined]
    async with httpx.AsyncClient(transport=remote, base_url="http://127.0.0.1") as c:
        assert (await c.get("/demo/authenticator", headers=hdr("usr_priya"))).status_code == 404
    for extra in (
        {"Host": "evil.example"},
        {"X-Forwarded-For": "203.0.113.5"},
        {"Via": "1.1 proxy"},
    ):
        r = await client.get("/demo/authenticator", headers={**hdr("usr_priya"), **extra})
        assert r.status_code == 404, extra


async def test_dev_helpers_refuse_proxied_requests(client: httpx.AsyncClient) -> None:
    for header in ({"X-Forwarded-For": "203.0.113.5"}, {"Forwarded": "for=203.0.113.5"}):
        assert (
            await client.get("/dev/totp", headers={**hdr("usr_priya"), **header})
        ).status_code == 404
        assert (await client.get("/dev/chat", headers=header)).status_code == 404


# ---- uploads ------------------------------------------------------------------------------------------

from reader_helpers import FIX, invoice_raw, poisoned_raw  # noqa: E402


async def test_attachment_upload_returns_a_typed_summary_and_the_doc_works_in_chat(
    client: httpx.AsyncClient, service: AgentService, sandbox
) -> None:
    from sqlalchemy import text as _t

    with sandbox.begin() as c:
        c.execute(_t("UPDATE accounts SET balance_minor = 2000000 WHERE id = 'acc_priya_aud'"))
    service.deps.gateway.reader_override = poisoned_raw()  # type: ignore[attr-defined]
    data = (FIX / "invoice_poisoned.pdf").read_bytes()  # a PDF: rasterised, text layer unused
    r = await client.post("/attachments", content=data, headers=hdr("usr_priya"))
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "invoice" and body["injection_suspected"] is True
    assert "differs from stored beneficiary" in body["summary"] and "HARBOURVIEW" not in json.dumps(
        body
    )
    chat = await client.post(
        "/chat",
        json={"message": "pay this invoice", "attachments": [{"doc_id": body["doc_id"]}]},
        headers=hdr("usr_priya"),
    )
    types_ = [e["type"] for e in parse_sse(chat.text)]
    assert "policy_decision" in types_ and "confirm_request" not in types_
    again = await client.post(
        "/chat",
        json={"message": "x", "attachments": [{"doc_id": body["doc_id"]}]},
        headers=hdr("usr_ramesh"),
    )
    assert any(e["type"] == "rejected" for e in parse_sse(again.text))  # not Ramesh's document


async def test_attachment_upload_refuses_bad_files_and_needs_a_token(
    client: httpx.AsyncClient, service: AgentService
) -> None:
    assert (await client.post("/attachments", content=b"\x89PNG\r\n\x1a\n")).status_code == 401
    txt = await client.post(
        "/attachments", content=b"hello, I am text named invoice.png", headers=hdr("usr_priya")
    )
    assert txt.status_code == 415 and txt.json()["detail"] == "unsupported_type"
    assert (
        await client.post("/attachments", content=b"", headers=hdr("usr_priya"))
    ).status_code == 400
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (10 * 1024 * 1024 + 8192)
    assert (
        await client.post("/attachments", content=big, headers=hdr("usr_priya"))
    ).status_code == 413
    corrupt = await client.post(
        "/attachments", content=b"\x89PNG\r\n\x1a\nnotreallyapng", headers=hdr("usr_priya")
    )
    assert corrupt.status_code == 400


async def test_attachment_reader_failure_is_a_clean_502_and_stores_nothing(
    client: httpx.AsyncClient, service: AgentService, sandbox
) -> None:
    from sqlalchemy import text

    from corridor.llm import LLMTimeout

    service.deps.gateway.reader_override = LLMTimeout("slow")  # type: ignore[attr-defined]
    r = await client.post(
        "/attachments", content=(FIX / "invoice_clean.png").read_bytes(), headers=hdr("usr_priya")
    )
    assert r.status_code == 502 and r.json()["detail"] == "reader_unavailable"
    with sandbox.connect() as c:
        assert c.execute(text("SELECT count(*) FROM documents")).scalar_one() == 0


async def test_a_chat_needs_a_message_a_document_or_forwarded_text(
    client: httpx.AsyncClient,
) -> None:
    assert (
        await client.post("/chat", json={"message": "  "}, headers=hdr("usr_priya"))
    ).status_code == 422
    ok = await client.post(
        "/chat", json={"message": "", "forwarded": "hello"}, headers=hdr("usr_priya")
    )
    assert ok.status_code == 200
    assert invoice_raw is not None


async def test_a_button_action_alone_is_a_valid_chat_request_and_needs_no_message(
    client: httpx.AsyncClient, service: AgentService
) -> None:
    body = {
        "action": {
            "kind": "pay_invoice",
            "invoice_id": "inv_priya_tuition_2026s2",
            "language": "en",
        }
    }
    r = await client.post("/chat", json=body, headers=hdr("usr_priya"))
    assert r.status_code == 200 and "confirm_request" in r.text
    assert service.deps.gateway.calls == []  # type: ignore[attr-defined]  # no model was asked
    bad = await client.post(
        "/chat", json={"action": {"kind": "send_money"}}, headers=hdr("usr_priya")
    )
    assert bad.status_code == 422
