"""The frozen API of P3-1 (docs/spec/api.md): contracts, role separation, officer data, pending proposals."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import Engine

from agent_helpers import TOKENS
from corridor.agent.service import AgentService
from corridor.api.app import create_app
from corridor.api.auth import TokenAuth
from corridor.config import AppEnv, Settings
from corridor.confirm import current_code
from corridor.contracts.api import EVENT_DATA_MODELS, ProposalTraceResponse

OFFICER = "officer-test-secret-0123456789abcdef"


def hdr(user: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[user]}"}


OFFICER_HDR = {"Authorization": f"Bearer {OFFICER}"}


@pytest.fixture
async def officer_app(
    service: AgentService, sandbox: Engine, tmp_path: Path
) -> AsyncIterator[tuple[httpx.AsyncClient, object]]:
    report = tmp_path / "20261012T000000"
    report.mkdir()
    (report / "report.json").write_text(
        json.dumps(
            {
                "meta": {"stamp": "headline: test"},
                "summary": {"full": {"summary": {"asr": {"mean": 0.0}}}},
                "summary_by_user_model": {"fooled_user": {}},
                "results": [
                    {
                        "id": "RT-1",
                        "mode": "baseline",
                        "user_model": "fooled_user",
                        "unauthorized": True,
                        "attack_success": True,
                        "unauthorized_npr_minor": 500,
                        "final_reply": "sent",
                        "rule_ids": [],
                    },
                    {
                        "id": "RT-1",
                        "mode": "full",
                        "user_model": "fooled_user",
                        "unauthorized": False,
                        "attack_success": False,
                        "new_payees": 0,
                        "final_reply": "refused",
                        "rule_ids": ["R-INVOICE-BENEFICIARY"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "latest.json").symlink_to("20261012T000000/report.json")
    from corridor.api import officer

    original = officer.REPORTS_DIR
    officer.REPORTS_DIR = tmp_path
    try:
        settings = Settings(app_env=AppEnv.DEV, frontend_origin="http://localhost:3000")
        app = create_app(
            settings=settings, service=service, engine=sandbox, auth=TokenAuth(TOKENS),
            today=lambda: date(2026, 10, 10), officer_token=OFFICER,
        )  # fmt: skip
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 5000))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
            yield c, app
    finally:
        officer.REPORTS_DIR = original


def officer_paths(app) -> list[tuple[str, str]]:  # type: ignore[no-untyped-def]
    out = []
    for r in app.routes:
        path = getattr(r, "path", "")
        if path.startswith("/officer"):
            for method in getattr(r, "methods", set()) - {"HEAD", "OPTIONS"}:
                out.append((method, path))
    return out


async def test_no_officer_endpoint_is_reachable_with_a_customer_token_or_none(officer_app) -> None:  # type: ignore[no-untyped-def]
    c, app = officer_app
    paths = officer_paths(app)
    assert (
        len(paths) >= 7
    )  # verify payee, ledger, ledger verify, trace, risk queue, compare, eval summary
    for method, path in paths:
        url = (
            path.replace("{payee_id}", "pay_x")
            .replace("{proposal_hash}", "0" * 64)
            .replace("{case_id}", "RT-1")
        )
        for headers in ({}, hdr("usr_priya"), hdr("usr_ramesh"), {"Authorization": "Bearer nope"}):
            r = await c.request(
                method,
                url,
                headers=headers,
                json={"method": "in_branch_check", "reference": "xyz"}
                if method == "POST"
                else None,
            )
            assert r.status_code == 401, (method, path, r.status_code)


async def test_officer_endpoints_are_off_when_officer_access_is_off(
    client: httpx.AsyncClient,
) -> None:
    for method, url in (
        ("GET", "/officer/ledger"),
        ("GET", "/officer/risk/queue"),
        ("GET", "/officer/eval/summary"),
    ):
        assert (await client.request(method, url, headers=hdr("usr_priya"))).status_code == 404
        assert (await client.request(method, url, headers=OFFICER_HDR)).status_code == 404


async def test_officer_sees_the_ledger_the_chain_and_a_full_proposal_trace(
    officer_app, sandbox: Engine
) -> None:  # type: ignore[no-untyped-def]
    c, _ = officer_app
    r = await c.post(
        "/chat", json={"message": "Ama lai 40 hajar pathau"}, headers=hdr("usr_ramesh")
    )
    events = [
        json.loads(b.split("data: ", 1)[1]) for b in r.text.strip().split("\n\n") if "data: " in b
    ]
    card = events[-1]["data"]
    thread = r.headers["x-thread-id"]
    code = current_code("usr_ramesh", datetime.now(UTC), sandbox.secrets_dir)  # type: ignore[attr-defined]
    done = await c.post(
        f"/chat/{thread}/confirm",
        json={"proposal_hash": card["proposal_hash"], "totp": code},
        headers=hdr("usr_ramesh"),
    )
    assert "executed" in done.text

    led = (await c.get("/officer/ledger", headers=OFFICER_HDR)).json()
    assert led["chain"]["valid"] is True and led["entries"][0]["seq"] > led["entries"][-1]["seq"]
    assert (await c.get("/officer/ledger/verify", headers=OFFICER_HDR)).json()["valid"] is True
    only = (await c.get("/officer/ledger?user=usr_priya", headers=OFFICER_HDR)).json()["entries"]
    assert all(e["payload"].get("user_id") == "usr_priya" for e in only)

    trace = ProposalTraceResponse.model_validate(
        (
            await c.get(f"/officer/proposals/{card['proposal_hash']}/trace", headers=OFFICER_HDR)
        ).json()
    )
    assert trace.found and trace.user_id == "usr_ramesh" and trace.chain and trace.chain.valid
    assert trace.proposal is not None and trace.proposal["action"] == "transfer"
    assert trace.decisions and "R-FLOOR" in trace.decisions[-1].rule_ids
    assert trace.decisions[-1].risk is not None and trace.decisions[-1].risk.score_bps >= 0
    assert trace.grant is not None and trace.execution is not None
    url = f"/officer/proposals/{card['proposal_hash']}/trace"
    for headers in (
        {},
        hdr("usr_ramesh"),
        hdr("usr_priya"),
    ):  # the trace (and parser text) is officer only
        assert (await c.get(url, headers=headers)).status_code == 401
    assert trace.parser_outputs  # the console gets the masked parser text by its ledger hash
    miss = (await c.get(f"/officer/proposals/{'0' * 64}/trace", headers=OFFICER_HDR)).json()
    assert miss["found"] is False
    assert (await c.get("/officer/proposals/short/trace", headers=OFFICER_HDR)).status_code == 422


async def test_risk_queue_lists_denied_and_high_risk_proposals_newest_first(officer_app) -> None:  # type: ignore[no-untyped-def]
    c, _ = officer_app
    await c.post("/chat", json={"message": "Ama lai 40 hajar pathau"}, headers=hdr("usr_ramesh"))
    # a request over the daily limit is denied by policy and must appear in the queue
    big = await c.post(
        "/chat", json={"message": "Ama lai 99999999 hajar pathau"}, headers=hdr("usr_ramesh")
    )
    assert big.status_code == 200
    q = (await c.get("/officer/risk/queue", headers=OFFICER_HDR)).json()["items"]
    seqs = [i["seq"] for i in q]
    assert seqs == sorted(seqs, reverse=True)
    assert all(i["decision"] == "deny" or (i["risk"] and i["risk"]["level"] != "low") for i in q)


async def test_case_comparison_and_eval_summary_come_from_the_latest_report(officer_app) -> None:  # type: ignore[no-untyped-def]
    c, _ = officer_app
    cmp = (await c.get("/officer/compare/RT-1", headers=OFFICER_HDR)).json()
    assert (
        cmp["found"]
        and cmp["baseline"][0]["outcome"] == "harmful"
        and cmp["full"][0]["outcome"] == "blocked"
    )
    assert (await c.get("/officer/compare/RT-404", headers=OFFICER_HDR)).json()["found"] is False
    summary = (await c.get("/officer/eval/summary", headers=OFFICER_HDR)).json()
    assert summary["found"] and summary["stamp"] == "headline: test"


async def test_routes_compare_and_tuition_plan_return_the_documented_shapes(
    client: httpx.AsyncClient,
) -> None:
    r = await client.post(
        "/routes/compare",
        json={
            "from_currency": "QAR",
            "to_currency": "NPR",
            "amount_minor": 80000,
            "deadline": "2026-10-21",
        },
        headers=hdr("usr_ramesh"),
    )
    d = r.json()
    assert (
        r.status_code == 200
        and d["status"] == "ok"
        and d["recommended"]["route"] == "KathmanduLink"
    )
    assert d["saving_vs_most_expensive"].startswith("QAR")
    none = (
        await client.post(
            "/routes/compare",
            json={
                "from_currency": "QAR",
                "to_currency": "NPR",
                "amount_minor": 9000000,
                "deadline": "2026-10-11",
            },
            headers=hdr("usr_ramesh"),
        )
    ).json()
    assert (
        none["status"] == "deadline_unmet"
        and "recommended" not in none
        and none["fastest_available"]
    )
    bad = await client.post(
        "/routes/compare",
        json={"from_currency": "QAR", "to_currency": "NPR", "amount_minor": 0},
        headers=hdr("usr_ramesh"),
    )
    assert bad.status_code == 422
    plan = (
        await client.get(
            "/tuition/plan?invoice_id=inv_priya_tuition_2026s2", headers=hdr("usr_priya")
        )
    ).json()
    assert plan["status"] == "ok" and [s["type"] for s in plan["steps"]] == [
        "request_contribution",
        "pay_invoice",
        "pay_invoice",
    ]
    other = (
        await client.get(
            "/tuition/plan?invoice_id=inv_priya_tuition_2026s2", headers=hdr("usr_ramesh")
        )
    ).json()
    assert other["found"] is False and other["steps"] == []
    assert (await client.get("/tuition/plan?invoice_id=x")).status_code == 401


async def test_pending_proposals_list_chat_and_passport_requests_of_the_caller_only(
    client: httpx.AsyncClient,
) -> None:
    r = await client.post(
        "/chat", json={"message": "Ama lai 40 hajar pathau"}, headers=hdr("usr_ramesh")
    )
    thread = r.headers["x-thread-id"]
    mine = (await client.get("/proposals/pending", headers=hdr("usr_ramesh"))).json()["proposals"]
    assert [p["thread_id"] for p in mine] == [thread] and mine[0]["kind"] == "chat"
    assert (
        mine[0]["required_level"] == "mfa"
        and mine[0]["card"]["display"]
        and mine[0]["risk"]["level"] == "low"
    )
    assert (await client.get("/proposals/pending", headers=hdr("usr_priya"))).json()[
        "proposals"
    ] == []
    await client.post(f"/chat/{thread}/cancel", headers=hdr("usr_ramesh"))
    assert (await client.get("/proposals/pending", headers=hdr("usr_ramesh"))).json()[
        "proposals"
    ] == []
    issue = await client.post("/passport/issue", headers=hdr("usr_priya"))
    pend = (await client.get("/proposals/pending", headers=hdr("usr_priya"))).json()["proposals"]
    assert [p["proposal_hash"] for p in pend] == [issue.json()["proposal_hash"]] and pend[0][
        "kind"
    ] == "passport"


async def test_streamed_event_data_matches_the_typed_contracts(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/chat", json={"message": "Ama lai 40 hajar pathau"}, headers=hdr("usr_ramesh")
    )
    events = [
        json.loads(b.split("data: ", 1)[1]) for b in r.text.strip().split("\n\n") if "data: " in b
    ]
    seen = set()
    for e in events:
        model = EVENT_DATA_MODELS.get(e["type"])
        if model is not None:
            model.model_validate(e["data"])
            seen.add(e["type"])
    assert {"policy_decision", "confirm_request"} <= seen


async def test_payees_and_accounts_use_the_typed_shape_and_the_fixed_label(
    client: httpx.AsyncClient,
) -> None:
    pay = (await client.get("/payees", headers=hdr("usr_priya"))).json()["payees"]
    assert pay and all(
        {"name_untrusted", "provenance", "verified", "added_via"} <= set(p) for p in pay
    )
    acc = (await client.get("/accounts", headers=hdr("usr_priya"))).json()["accounts"]
    assert {a["kind"] for a in acc} == {"spending", "savings"}


def test_the_generated_typescript_is_up_to_date() -> None:
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[4]
    out = subprocess.run(
        [sys.executable, str(root / "scripts" / "gen_contracts_ts.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stdout + out.stderr


# ---------------------------------------------------------------- P3-2: bank verification path (ADR 0018)
@pytest.fixture
def typed_payee(sandbox: Engine):  # type: ignore[no-untyped-def]
    """A payee Priya typed an hour ago; removed afterwards so whole-seed comparisons stay clean."""
    from sqlalchemy import text

    pid = _insert_typed_payee(sandbox)
    yield pid
    with sandbox.begin() as c:
        c.execute(text("DELETE FROM verification_requests"))
        c.execute(text("DELETE FROM payees WHERE id = 'pay_typed_test'"))


def _insert_typed_payee(sandbox: Engine, name: str = "Okafor Property") -> str:
    from sqlalchemy.orm import Session

    from corridor.contracts import Provenance
    from corridor.sandbox import vault
    from corridor.sandbox.models import Payee

    with Session(sandbox) as s, s.begin():
        s.add(
            Payee(
                id="pay_typed_test", user_id="usr_priya", display_name=name, region="AU",
                currency="AUD", provider_ref=None, relation=None, account_no_masked="****1234",
                account_no_hash=vault.account_hash("062000771234", sandbox.secrets_dir),  # type: ignore[attr-defined]
                verified_at=None, added_via="agent_mfa", created_at=datetime.now(UTC),
                provenance=Provenance.USER_TYPED.value,
            )
        )  # fmt: skip
    return "pay_typed_test"


async def test_a_customer_can_ask_the_bank_to_verify_only_their_own_payee(
    officer_app, sandbox: Engine, typed_payee: str
) -> None:  # type: ignore[no-untyped-def]
    c, _ = officer_app
    pid = typed_payee
    assert (
        await c.post(f"/payees/{pid}/verification-request", headers=hdr("usr_ramesh"))
    ).status_code == 404
    assert (await c.post(f"/payees/{pid}/verification-request")).status_code == 401
    r = await c.post(f"/payees/{pid}/verification-request", headers=hdr("usr_priya"))
    assert r.status_code == 200 and r.json()["status"] == "pending"
    again = await c.post(f"/payees/{pid}/verification-request", headers=hdr("usr_priya"))
    assert again.json()["request_id"] == r.json()["request_id"]  # one open request per payee
    mine = (await c.get("/payees", headers=hdr("usr_priya"))).json()["payees"]
    row = next(p for p in mine if p["payee_id"] == pid)
    assert (
        row["verification"] == "pending" and row["hold_until"] is not None and not row["verified"]
    )
    # asking never verifies: the customer has no way to verify anything
    assert (
        await c.post(
            f"/officer/payees/{pid}/verify",
            json={"method": "in_branch_check", "reference": "xyz"},
            headers=hdr("usr_priya"),
        )
    ).status_code == 401


async def test_the_officer_queue_shows_the_cop_result_and_verifying_needs_a_match(
    officer_app, sandbox: Engine, typed_payee: str
) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import delete
    from sqlalchemy.orm import Session

    from corridor.sandbox import vault
    from corridor.sandbox.models import CopRegistry

    c, _ = officer_app
    pid = typed_payee
    await c.post(f"/payees/{pid}/verification-request", headers=hdr("usr_priya"))
    h = vault.account_hash("062000771234", sandbox.secrets_dir)  # type: ignore[attr-defined]
    with Session(sandbox) as s, s.begin():
        s.execute(delete(CopRegistry).where(CopRegistry.account_no_hash == h))
    q = (await c.get("/officer/verification-requests", headers=OFFICER_HDR)).json()["items"]
    assert [i["cop"] for i in q] == ["no_record"] and q[0]["payee_name"] == "Okafor Property"
    refused = await c.post(
        f"/officer/payees/{pid}/verify",
        json={"method": "in_branch_check", "reference": "lease seen"},
        headers=OFFICER_HDR,
    )
    assert refused.status_code == 409 and refused.json()["detail"] == "cop_no_record"
    assert (await c.get("/officer/verification-requests", headers=OFFICER_HDR)).json()[
        "items"
    ] == []
    closed = (
        await c.get("/officer/verification-requests?status=refused", headers=OFFICER_HDR)
    ).json()
    assert [i["status"] for i in closed["items"]] == [
        "refused"
    ]  # the refusal closed it, the reason is ledgered
    again = await c.post(f"/payees/{pid}/verification-request", headers=hdr("usr_priya"))
    assert again.json()["status"] == "pending"  # the customer may ask again
    with Session(sandbox) as s, s.begin():
        s.add(CopRegistry(account_no_hash=h, holder_name="Okafor Property Pty Ltd", synthetic=True))
    q = (await c.get("/officer/verification-requests", headers=OFFICER_HDR)).json()["items"]
    assert q[0]["cop"] == "match"
    ok = await c.post(
        f"/officer/payees/{pid}/verify",
        json={"method": "callback_independent_number", "reference": "CoP name match, lease seen"},
        headers=OFFICER_HDR,
    )
    assert ok.status_code == 200
    assert (await c.get("/officer/verification-requests", headers=OFFICER_HDR)).json()[
        "items"
    ] == []
    done = (
        await c.get("/officer/verification-requests?status=verified", headers=OFFICER_HDR)
    ).json()["items"]
    assert done[0]["status"] == "verified"
    row = next(
        p
        for p in (await c.get("/payees", headers=hdr("usr_priya"))).json()["payees"]
        if p["payee_id"] == pid
    )
    assert (
        row["verification"] == "verified" and row["hold_until"] is None
    )  # a typed payee is released


# ---------------------------------------------------------------- P3-2: contributor accepts or declines
async def test_the_contributor_answers_a_request_and_nobody_else_can(
    officer_app, sandbox: Engine
) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from corridor.sandbox.models import ContributionRequest

    c, _ = officer_app
    with Session(sandbox) as s, s.begin():
        s.add(
            ContributionRequest(
                id="cr_test_1", requester_user_id="usr_priya", contributor_user_id="usr_ramesh",
                amount_minor=250_000, currency="QAR", target_account_id="acc_priya_aud",
                invoice_id="inv_priya_tuition_2026s2", status="pending", idempotency_key="k_cr_test_1",
                proposal_hash="0" * 64, created_at=datetime.now(UTC),
            )
        )  # fmt: skip
    mine = (await c.get("/contributions", headers=hdr("usr_ramesh"))).json()
    assert [r["request_id"] for r in mine["incoming"]] == ["cr_test_1"] and mine["outgoing"] == []
    assert (
        mine["incoming"][0]["amount"] == "QAR 2,500.00"
        and mine["incoming"][0]["status"] == "pending"
    )
    theirs = (await c.get("/contributions", headers=hdr("usr_priya"))).json()
    assert theirs["incoming"] == [] and [r["request_id"] for r in theirs["outgoing"]] == [
        "cr_test_1"
    ]
    # the requester cannot answer her own request, and a stranger cannot see it
    assert (
        await c.post(
            "/contributions/cr_test_1/respond", json={"accept": True}, headers=hdr("usr_priya")
        )
    ).status_code == 404
    assert (
        await c.post("/contributions/cr_test_1/respond", json={"accept": True})
    ).status_code == 401
    done = await c.post(
        "/contributions/cr_test_1/respond", json={"accept": True}, headers=hdr("usr_ramesh")
    )
    assert done.status_code == 200 and done.json()["status"] == "accepted"
    again = await c.post(
        "/contributions/cr_test_1/respond", json={"accept": False}, headers=hdr("usr_ramesh")
    )
    assert again.status_code == 409  # answered once
    with sandbox.connect() as conn:  # it moved no money and it is ledgered
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM ledger_entries WHERE event_type = 'contribution_responded'"
                )
            ).scalar_one()
            == 1
        )
        assert (
            conn.execute(
                text("SELECT balance_minor FROM accounts WHERE id = 'acc_priya_aud'")
            ).scalar_one()
            > 0
        )
    with sandbox.begin() as conn:
        conn.execute(text("DELETE FROM contribution_requests WHERE id = 'cr_test_1'"))


async def test_a_payee_you_typed_is_listed_under_your_own_words_a_document_one_under_the_fixed_label(
    officer_app, sandbox: Engine, typed_payee: str
) -> None:  # type: ignore[no-untyped-def]
    c, _ = officer_app
    rows = {
        p["payee_id"]: p
        for p in (await c.get("/payees", headers=hdr("usr_priya"))).json()["payees"]
    }
    typed = rows[typed_payee]
    assert typed["display_name"] == "Okafor Property" and typed["name_untrusted"] is False
    assert typed["provenance"] == "user_typed"


# ---------------------------------------------------------------- P3-3: one credential per officer (ADR 0019)
async def test_each_officer_has_their_own_credential_and_their_id_is_ledgered(
    sandbox: Engine, service
) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import text

    tokens = {
        "officer_asha": "asha-secret-0123456789abcdef",
        "officer_binod": "binod-secret-0123456789abcdef",
    }
    app = create_app(
        settings=Settings(app_env=AppEnv.DEV, frontend_origin="http://localhost:3000"), service=service,
        engine=sandbox, auth=TokenAuth(TOKENS), today=lambda: date(2026, 10, 10), officer_token=tokens,
    )  # fmt: skip
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 5000))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
        for oid, tok in tokens.items():
            me = await c.get("/officer/me", headers={"Authorization": f"Bearer {tok}"})
            assert me.json() == {"officer_id": oid}
        for bad in ({}, hdr("usr_priya"), {"Authorization": "Bearer asha-secret"}):
            assert (await c.get("/officer/me", headers=bad)).status_code == 401
        body = {"method": "in_branch_check", "reference": "no such payee, refused"}
        for (
            oid,
            tok,
        ) in tokens.items():  # a refused attempt is ledgered under the officer who made it
            await c.post(
                "/officer/payees/pay_nope/verify",
                json=body,
                headers={"Authorization": f"Bearer {tok}"},
            )
        with sandbox.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT payload_canonical_json FROM ledger_entries WHERE event_type = 'rejection'"
                )
            ).all()
        blob = " ".join(r[0] for r in rows)
        assert "officer_asha" in blob and "officer_binod" in blob


# ---------------------------------------------------------------- P3-3: console data (ADR 0019)
async def test_the_trace_lists_every_rule_that_spoke_and_the_whole_turn(
    officer_app, service
) -> None:  # type: ignore[no-untyped-def]
    from agent_helpers import collect
    from corridor.contracts.events import AgentEventType as ET

    c, _ = officer_app
    events = await collect(
        service.chat("usr_priya", "Add my new landlord Okafor Property, account 062000771234")
    )
    card = next(e for e in events if e.type is ET.CONFIRM_REQUEST).data
    t = (
        await c.get(f"/officer/proposals/{card['proposal_hash']}/trace", headers=OFFICER_HDR)
    ).json()
    assert t["found"] and t["decisions"], t
    rules = {r[0]: r[1] for r in t["decisions"][0]["rule_results"]}
    assert rules["R-PAYEE-ADD"] == "require_mfa"  # each rule with its own result
    kinds = [e["event_type"] for e in t["trace_entries"]]
    assert "proposal" in kinds and "policy_decision" in kinds
    assert all(e["trace_id"] == t["trace_entries"][0]["trace_id"] for e in t["trace_entries"])


async def test_the_chain_check_fails_after_one_row_is_tampered(
    officer_app, sandbox: Engine, service
) -> None:  # type: ignore[no-untyped-def]
    from agent_helpers import collect
    from ledger_helpers import admin_sql

    c, _ = officer_app
    await collect(
        service.chat("usr_priya", "Add my new landlord Okafor Property, account 062000771234")
    )
    ok = (await c.get("/officer/ledger/verify", headers=OFFICER_HDR)).json()
    assert ok["valid"] is True and ok["entries_checked"] >= 2
    admin_sql(
        sandbox,
        "UPDATE ledger_entries SET payload_canonical_json = payload_canonical_json || ' ' WHERE seq = 2",
    )
    bad = (await c.get("/officer/ledger/verify", headers=OFFICER_HDR)).json()
    assert bad["valid"] is False and bad["first_broken_seq"] == 2


async def test_cases_compare_and_the_headline_are_read_only_from_the_report(officer_app) -> None:  # type: ignore[no-untyped-def]
    c, _ = officer_app
    cases = (await c.get("/officer/cases", headers=OFFICER_HDR)).json()
    assert (
        cases["found"]
        and [x["id"] for x in cases["cases"]] == ["RT-1"]
        and cases["report"] == "20261012T000000"
    )
    one = (await c.get("/officer/compare/RT-1", headers=OFFICER_HDR)).json()
    assert one["report_stamp"] == "headline: test"
    base, full = one["baseline"][0], one["full"][0]
    assert (
        base["unauthorized_npr"] == "NPR 5.00" and base["outcome"] == "harmful"
    )  # 500 paisa, formatted
    assert full["outcome"] == "blocked" and full["unauthorized_npr"] == "NPR 0.00"
    summary = (await c.get("/officer/eval/summary", headers=OFFICER_HDR)).json()
    assert (
        summary["report"] == "20261012T000000" and summary["headline"] == []
    )  # the test report has no per-model numbers


async def test_the_ledger_can_be_filtered_by_event_type_and_proposal(officer_app, service) -> None:  # type: ignore[no-untyped-def]
    from agent_helpers import collect
    from corridor.contracts.events import AgentEventType as ET

    c, _ = officer_app
    events = await collect(
        service.chat("usr_priya", "Add my new landlord Okafor Property, account 062000771234")
    )
    h = next(e for e in events if e.type is ET.CONFIRM_REQUEST).data["proposal_hash"]
    only = (await c.get("/officer/ledger?event_type=proposal", headers=OFFICER_HDR)).json()[
        "entries"
    ]
    assert only and {e["event_type"] for e in only} == {"proposal"}
    by_hash = (await c.get(f"/officer/ledger?proposal_hash={h}", headers=OFFICER_HDR)).json()[
        "entries"
    ]
    assert {e["payload"]["proposal_hash"] for e in by_hash} == {h}
    assert (await c.get("/officer/ledger?proposal_hash=zz", headers=OFFICER_HDR)).status_code == 422
