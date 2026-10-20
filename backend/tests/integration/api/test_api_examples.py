"""Real request and response bodies for docs/spec/api.md.

Run with ``API_EXAMPLES=../logs/api_examples.json uv run pytest tests/integration/api/test_api_examples.py``
to dump them; without the variable the test only checks that every response validates against its
contract model, so the documented shapes cannot drift from the code."""

import json
import os
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import Engine
from test_frozen_api import OFFICER, OFFICER_HDR, hdr

from agent_helpers import TOKENS
from corridor.agent.service import AgentService
from corridor.api.app import create_app
from corridor.api.auth import TokenAuth
from corridor.config import AppEnv, Settings
from corridor.confirm import current_code
from corridor.contracts import api as contracts


def sse(body: str) -> list[dict]:  # type: ignore[type-arg]
    return [
        json.loads(b.split("data: ", 1)[1]) for b in body.strip().split("\n\n") if "data: " in b
    ]


def short(obj, n=12):  # type: ignore[no-untyped-def]
    """Trim long lists for the document."""
    if isinstance(obj, dict):
        return {k: short(v, n) for k, v in obj.items()}
    if isinstance(obj, list):
        return [short(v, n) for v in obj[:n]]
    return obj


@pytest.fixture
async def app_client(service: AgentService, sandbox: Engine):  # type: ignore[no-untyped-def]
    settings = Settings(app_env=AppEnv.DEV, frontend_origin="http://localhost:3000")
    app = create_app(
        settings=settings, service=service, engine=sandbox, auth=TokenAuth(TOKENS),
        today=lambda: date(2026, 10, 10), officer_token=OFFICER,
    )  # fmt: skip
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 5000))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
        yield c


async def test_documented_examples_validate_and_can_be_dumped(app_client, sandbox: Engine) -> None:  # type: ignore[no-untyped-def]
    c = app_client
    ex: dict[str, object] = {}

    def keep(name: str, req: object, resp: object) -> None:
        ex[name] = {"request": req, "response": short(resp)}

    r = await c.post(
        "/chat", json={"message": "Ama lai 40 hajar pathau"}, headers=hdr("usr_ramesh")
    )
    events = sse(r.text)
    thread = r.headers["x-thread-id"]
    ex["chat_events"] = {
        "request": {"message": "Ama lai 40 hajar pathau"},
        "response_headers": {"X-Thread-Id": thread},
        "events": [
            e | {"data": short(e["data"], 4)}
            for e in events
            if e["type"] in ("intent", "route", "policy_decision", "confirm_request", "message")
        ],
    }
    card = next(e for e in events if e["type"] == "confirm_request")["data"]
    contracts.ConfirmRequestData.model_validate(card)
    pend = (await c.get("/proposals/pending", headers=hdr("usr_ramesh"))).json()
    contracts.PendingProposalsResponse.model_validate(pend)
    keep("proposals_pending", None, pend)
    code = current_code("usr_ramesh", datetime.now(UTC), sandbox.secrets_dir)  # type: ignore[attr-defined]
    done = await c.post(
        f"/chat/{thread}/confirm",
        json={"proposal_hash": card["proposal_hash"], "totp": code},
        headers=hdr("usr_ramesh"),
    )
    confirm_events = sse(done.text)
    ex["chat_confirm"] = {
        "request": {"proposal_hash": card["proposal_hash"], "totp": "123456"},
        "events": [
            e | {"data": short(e["data"], 4)}
            for e in confirm_events
            if e["type"] in ("executed", "message")
        ],
    }
    for name, path, model, user in (
        ("accounts", "/accounts", contracts.AccountsResponse, "usr_priya"),
        ("payees", "/payees", contracts.PayeesResponse, "usr_priya"),
    ):
        body = (await c.get(path, headers=hdr(user))).json()
        model.model_validate(body)
        keep(name, None, body)
    req = {
        "from_currency": "QAR",
        "to_currency": "NPR",
        "amount_minor": 80000,
        "deadline": "2026-10-21",
    }
    body = (await c.post("/routes/compare", json=req, headers=hdr("usr_ramesh"))).json()
    contracts.RouteComparisonResponse.model_validate(body)
    keep("routes_compare", req, body)
    body = (
        await c.get("/tuition/plan?invoice_id=inv_priya_tuition_2026s2", headers=hdr("usr_priya"))
    ).json()
    contracts.TuitionPlanResponse.model_validate(body)
    keep("tuition_plan", {"invoice_id": "inv_priya_tuition_2026s2"}, body)

    issue = (await c.post("/passport/issue", headers=hdr("usr_priya"))).json()
    contracts.PassportProposalResponse.model_validate(issue)
    keep("passport_issue", None, issue)
    issued = (
        await c.post(
            "/passport/confirm",
            json={"proposal_hash": issue["proposal_hash"]},
            headers=hdr("usr_priya"),
        )
    ).json()
    contracts.PassportConfirmResponse.model_validate(issued)
    keep("passport_confirm_issue", {"proposal_hash": issue["proposal_hash"]}, issued)
    lst = (await c.get("/passport", headers=hdr("usr_priya"))).json()
    contracts.PassportListResponse.model_validate(lst)
    keep("passport_list", None, lst)
    preq = {
        "passport_id": issued["passport_id"],
        "claims": ["rent_on_time"],
        "audience": "new landlord",
    }
    pres = (await c.post("/passport/present", json=preq, headers=hdr("usr_priya"))).json()
    contracts.PassportProposalResponse.model_validate(pres)
    keep("passport_present", preq, pres)
    code = current_code("usr_priya", datetime.now(UTC), sandbox.secrets_dir)  # type: ignore[attr-defined]
    shared = (
        await c.post(
            "/passport/confirm",
            json={"proposal_hash": pres["proposal_hash"], "totp": code},
            headers=hdr("usr_priya"),
        )
    ).json()
    contracts.PassportConfirmResponse.model_validate(shared)
    keep(
        "passport_confirm_share", {"proposal_hash": pres["proposal_hash"], "totp": "123456"}, shared
    )
    ver = (
        await c.post(
            "/passport/verify",
            json={"presentation": shared["presentation"], "expected_audience": shared["audience"]},
        )
    ).json()
    contracts.PassportVerifyResponse.model_validate(ver)
    keep(
        "passport_verify",
        {"presentation": "<the presentation above>", "expected_audience": shared["audience"]},
        ver,
    )

    led = (await c.get("/officer/ledger?limit=3", headers=OFFICER_HDR)).json()
    contracts.LedgerViewResponse.model_validate(led)
    keep(
        "officer_ledger",
        None,
        {
            "entries": [e | {"payload": short(e["payload"], 3)} for e in led["entries"][:2]],
            "chain": led["chain"],
        },
    )
    trace = (
        await c.get(f"/officer/proposals/{card['proposal_hash']}/trace", headers=OFFICER_HDR)
    ).json()
    contracts.ProposalTraceResponse.model_validate(trace)
    keep("officer_trace", None, {k: v for k, v in trace.items() if k != "entries"})
    q = (await c.get("/officer/risk/queue?limit=3", headers=OFFICER_HDR)).json()
    contracts.RiskQueueResponse.model_validate(q)
    keep("officer_risk_queue", None, q)
    ver_chain = (await c.get("/officer/ledger/verify", headers=OFFICER_HDR)).json()
    contracts.ChainStatus.model_validate(ver_chain)
    keep("officer_ledger_verify", None, ver_chain)
    for path, model in (
        ("/officer/compare/RT-001", contracts.CaseComparisonResponse),
        ("/officer/eval/summary", contracts.EvalSummaryResponse),
    ):
        body = (await c.get(path, headers=OFFICER_HDR)).json()
        model.model_validate(body)
        keep(
            path.strip("/").replace("/", "_"),
            None,
            {k: v for k, v in body.items() if k not in ("summary", "summary_by_user_model")}
            | ({"summary": "<per mode metrics>"} if "summary" in body else {}),
        )

    target = os.environ.get("API_EXAMPLES")
    if target:
        Path(target).write_text(json.dumps(ex, indent=2, ensure_ascii=False), encoding="utf-8")
