"""Credit Passport against the seeded sandbox: claims, executor, ledger, HTTP routes (P3-1, ADR 0015)."""

import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from agent_helpers import TOKENS
from corridor.confirm import current_code
from corridor.contracts import ActionType, Provenance, Tainted
from corridor.contracts.action import ActionProposal, PassportShareSpec
from corridor.contracts.confirm import ConfirmationLevel as L
from corridor.executor import ExecutionRejected, Executor, PassportDone
from corridor.ledger import verify_chain
from corridor.passport import credential
from corridor.passport.claims import build_claims
from corridor.sandbox import vault
from executor_helpers import CFG, NOW, count, grant_for, run


@pytest.fixture
def ex(sandbox: Engine) -> Executor:
    return Executor(sandbox, CFG, secrets_dir=sandbox.secrets_dir)  # type: ignore[attr-defined]


def proposal(
    action: ActionType, user: str = "usr_priya", share: PassportShareSpec | None = None
) -> ActionProposal:
    acct = {"usr_priya": "acc_priya_aud", "usr_ramesh": "acc_ramesh_qar"}[user]
    return ActionProposal(
        proposal_id=f"prop_{uuid.uuid4().hex[:8]}", action=action, user_id=user,
        source_account_id=Tainted[str](value=acct, provenance=Provenance.SYSTEM),
        passport_share=share, idempotency_key=f"idem-{uuid.uuid4().hex}",
    )  # fmt: skip


def issue(sandbox: Engine, ex: Executor, user: str = "usr_priya") -> PassportDone:
    p = proposal(ActionType.ISSUE_PASSPORT, user)
    r = run(ex, p, grant_for(sandbox, p, L.CONFIRM))
    assert isinstance(r, PassportDone)
    return r


def test_claims_come_from_the_history_and_hold_no_amounts_or_account_numbers(
    sandbox: Engine,
) -> None:
    with Session(sandbox) as s:
        claims = {c["id"]: c for c in build_claims(s, "usr_priya", date(2026, 10, 10))}
    assert (
        claims["rent_on_time"]["payments"] == 6 and claims["rent_on_time"]["on_time_payments"] == 5
    )
    assert claims["tuition_on_time"]["payments"] == 1
    assert "no_failed_payments" in claims and "history_months" in claims
    blob = str(claims)
    assert "amount" not in blob and "acc_" not in blob and "pay_" not in blob and "****" not in blob


def test_a_failed_payment_removes_the_no_failed_claim(sandbox: Engine) -> None:
    with sandbox.begin() as c:
        c.execute(
            text(
                "UPDATE transfers SET status = 'failed' WHERE user_id = 'usr_priya' AND id = (SELECT min(id) FROM transfers WHERE user_id = 'usr_priya')"
            )
        )
    with Session(sandbox) as s:
        assert "no_failed_payments" not in {
            c["id"] for c in build_claims(s, "usr_priya", date(2026, 10, 10))
        }


def test_issue_then_share_one_claim_is_ledgered_and_verifies(sandbox: Engine, ex: Executor) -> None:
    done = issue(sandbox, ex)
    assert "rent_on_time" in done.claim_ids and count(sandbox, "passports") == 1
    share = proposal(
        ActionType.SHARE_PASSPORT,
        share=PassportShareSpec(passport_id=done.passport_id, claim_ids=("rent_on_time",), audience="new landlord"),
    )  # fmt: skip
    r = run(ex, share, grant_for(sandbox, share, L.MFA))
    assert isinstance(r, PassportDone) and r.presentation is not None
    pub = credential.public_key_hex(vault.passport_signing_key_hex(sandbox.secrets_dir))  # type: ignore[attr-defined]
    v = credential.verify(
        r.presentation,
        trusted_public_key_hex=pub,
        now=datetime.now(UTC),
        expected_audience="new landlord",
    )
    assert (
        v.valid and [c["id"] for c in v.claims] == ["rent_on_time"] and v.audience == "new landlord"
    )
    assert count(sandbox, "ledger_entries", "event_type = 'passport_issued'") == 1
    assert count(sandbox, "ledger_entries", "event_type = 'passport_shared'") == 1
    with Session(sandbox) as s:
        assert verify_chain(s).valid


def test_sharing_needs_mfa_and_a_claim_the_passport_holds(sandbox: Engine, ex: Executor) -> None:
    done = issue(sandbox, ex)
    spec = PassportShareSpec(
        passport_id=done.passport_id, claim_ids=("rent_on_time",), audience="x"
    )
    weak = proposal(ActionType.SHARE_PASSPORT, share=spec)
    with pytest.raises(ExecutionRejected) as e:
        run(
            ex, weak, grant_for(sandbox, weak, L.CONFIRM)
        )  # a tap is not enough to share personal data
    assert e.value.reason.startswith("grant_")
    missing = proposal(
        ActionType.SHARE_PASSPORT,
        share=PassportShareSpec(passport_id=done.passport_id, claim_ids=("bills_on_time",), audience="x"),
    )  # fmt: skip
    with pytest.raises(ExecutionRejected) as e2:
        run(ex, missing, grant_for(sandbox, missing, L.MFA))
    assert "R-PASSPORT" in e2.value.rule_ids


def test_another_users_passport_cannot_be_shared(sandbox: Engine, ex: Executor) -> None:
    done = issue(sandbox, ex)
    steal = proposal(
        ActionType.SHARE_PASSPORT, "usr_ramesh",
        PassportShareSpec(passport_id=done.passport_id, claim_ids=("rent_on_time",), audience="x"),
    )  # fmt: skip
    with pytest.raises(ExecutionRejected) as e:
        run(ex, steal, grant_for(sandbox, steal, L.MFA))
    assert "R-PASSPORT" in e.value.rule_ids
    assert count(sandbox, "passport_presentations") == 0


def test_an_expired_passport_cannot_be_shared_and_a_replayed_key_does_nothing(
    sandbox: Engine, ex: Executor
) -> None:
    done = issue(sandbox, ex)
    spec = PassportShareSpec(
        passport_id=done.passport_id, claim_ids=("rent_on_time",), audience="x"
    )
    late = proposal(ActionType.SHARE_PASSPORT, share=spec)
    with pytest.raises(ExecutionRejected) as e:
        run(
            ex,
            late,
            grant_for(sandbox, late, L.MFA),
            now=NOW + timedelta(days=credential.VALIDITY_DAYS + 1),
        )
    assert "R-PASSPORT" in e.value.rule_ids
    ok = proposal(ActionType.SHARE_PASSPORT, share=spec)
    run(ex, ok, grant_for(sandbox, ok, L.MFA))
    with pytest.raises(ExecutionRejected):
        run(ex, ok, grant_for(sandbox, ok, L.MFA))  # same key
    assert count(sandbox, "passport_presentations") == 1


def hdr(user: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[user]}"}


async def test_http_flow_issue_present_confirm_verify(
    client: httpx.AsyncClient, sandbox: Engine
) -> None:
    r = await client.post("/passport/issue", headers=hdr("usr_priya"))
    assert r.status_code == 200 and r.json()["required_level"] == "confirm"
    done = await client.post(
        "/passport/confirm",
        json={"proposal_hash": r.json()["proposal_hash"]},
        headers=hdr("usr_priya"),
    )
    assert done.status_code == 200 and done.json()["kind"] == "issued"
    pid = done.json()["passport_id"]
    listed = (await client.get("/passport", headers=hdr("usr_priya"))).json()
    assert [p["passport_id"] for p in listed["passports"]] == [pid]
    assert not (await client.get("/passport", headers=hdr("usr_ramesh"))).json()["passports"]

    p = await client.post(
        "/passport/present", json={"passport_id": pid, "claims": ["rent_on_time"], "audience": "new landlord"},
        headers=hdr("usr_priya"),
    )  # fmt: skip
    assert p.status_code == 200 and p.json()["required_level"] == "mfa"
    h = p.json()["proposal_hash"]
    no_code = await client.post(
        "/passport/confirm", json={"proposal_hash": h}, headers=hdr("usr_priya")
    )
    assert no_code.status_code == 403  # MFA required; the proposal stays pending for a retry
    code = current_code("usr_priya", datetime.now(UTC), sandbox.secrets_dir)  # type: ignore[attr-defined]
    shared = await client.post(
        "/passport/confirm", json={"proposal_hash": h, "totp": code}, headers=hdr("usr_priya")
    )
    assert shared.status_code == 200
    pres = shared.json()["presentation"]
    # anyone can verify, no token needed
    wrong = (
        await client.post(
            "/passport/verify", json={"presentation": pres, "expected_audience": "someone else"}
        )
    ).json()
    assert (
        wrong["valid"] is False and wrong["reason"] == "wrong_audience"
    )  # forwarded: not for this verifier
    body = {"presentation": pres, "expected_audience": "new landlord"}
    v = (await client.post("/passport/verify", json=body)).json()
    assert v["valid"] and [c["id"] for c in v["claims"]] == ["rent_on_time"]
    again = (await client.post("/passport/verify", json=body)).json()
    assert (
        again["valid"] is False and again["reason"] == "nonce_reused"
    )  # the same presentation, twice
    tampered = {
        **pres,
        "disclosures": [
            {**pres["disclosures"][0], "claim": {**pres["disclosures"][0]["claim"], "payments": 99}}
        ],
    }
    bad = (
        await client.post(
            "/passport/verify", json={"presentation": tampered, "expected_audience": "new landlord"}
        )
    ).json()
    assert bad["valid"] is False and bad["reason"] == "claim_not_signed"


async def test_http_another_users_proposal_hash_cannot_be_confirmed_and_sharing_foreign_claims_is_denied(
    client: httpx.AsyncClient,
) -> None:
    r = await client.post("/passport/issue", headers=hdr("usr_priya"))
    steal = await client.post(
        "/passport/confirm",
        json={"proposal_hash": r.json()["proposal_hash"]},
        headers=hdr("usr_ramesh"),
    )
    assert steal.status_code == 409
    nothing = await client.post(
        "/passport/present",
        json={"passport_id": "pp_nope", "claims": ["rent_on_time"]},
        headers=hdr("usr_priya"),
    )
    assert nothing.status_code == 409 and nothing.json()["detail"]["reason"] == "policy_denied"
    assert (await client.post("/passport/issue")).status_code == 401


# ---------------------------------------------------------------- verifier issued challenge (ADR 0015)
async def _issue_and_share(
    client: httpx.AsyncClient, sandbox: Engine, audience: str, challenge: str | None
):  # type: ignore[no-untyped-def]
    issue = await client.post("/passport/issue", headers=hdr("usr_priya"))
    done = await client.post(
        "/passport/confirm",
        json={"proposal_hash": issue.json()["proposal_hash"]},
        headers=hdr("usr_priya"),
    )
    body = {
        "passport_id": done.json()["passport_id"],
        "claims": ["rent_on_time"],
        "audience": audience,
    }
    if challenge is not None:
        body["challenge"] = challenge
    return await client.post("/passport/present", json=body, headers=hdr("usr_priya"))


async def _confirm(client: httpx.AsyncClient, sandbox: Engine, proposed: httpx.Response):  # type: ignore[no-untyped-def]
    code = current_code("usr_priya", datetime.now(UTC), sandbox.secrets_dir)  # type: ignore[attr-defined]
    return await client.post(
        "/passport/confirm",
        json={"proposal_hash": proposed.json()["proposal_hash"], "totp": code},
        headers=hdr("usr_priya"),
    )


async def test_the_presentation_must_answer_the_verifiers_challenge_for_that_verifier(
    client: httpx.AsyncClient, sandbox: Engine
) -> None:
    ch = (await client.post("/passport/challenge", json={"audience": "new landlord"})).json()
    assert len(ch["nonce"]) == 32 and ch["audience"] == "new landlord"
    other = (await client.post("/passport/challenge", json={"audience": "a bank"})).json()
    wrong = await _issue_and_share(client, sandbox, "new landlord", other["nonce"])
    assert wrong.status_code == 409 and wrong.json()["detail"]["reason"] == "challenge_invalid"
    nothing = await _issue_and_share(client, sandbox, "new landlord", "f" * 32)
    assert nothing.status_code == 409  # a nonce nobody issued
    proposed = await _issue_and_share(client, sandbox, "new landlord", ch["nonce"])
    assert proposed.status_code == 200
    shared = await _confirm(client, sandbox, proposed)
    pres = shared.json()["presentation"]
    assert pres["nonce"] == ch["nonce"]  # the presentation carries the verifier's own nonce
    body = {"presentation": pres, "expected_audience": "new landlord"}
    assert (await client.post("/passport/verify", json=body)).json()["valid"] is True
    again = (await client.post("/passport/verify", json=body)).json()
    assert again["valid"] is False and again["reason"] == "nonce_reused"
    # the same challenge cannot be answered by a second proposal either
    assert (await _issue_and_share(client, sandbox, "new landlord", ch["nonce"])).status_code == 409


async def test_a_forwarded_presentation_fails_at_the_next_verifier(
    client: httpx.AsyncClient, sandbox: Engine
) -> None:
    ch = (await client.post("/passport/challenge", json={"audience": "landlord A"})).json()
    shared = await _confirm(
        client, sandbox, await _issue_and_share(client, sandbox, "landlord A", ch["nonce"])
    )
    pres = shared.json()["presentation"]
    b = await client.post(
        "/passport/verify", json={"presentation": pres, "expected_audience": "landlord B"}
    )
    assert b.json()["valid"] is False and b.json()["reason"] == "wrong_audience"
    relabelled = {**pres, "audience": "landlord B"}
    r = await client.post(
        "/passport/verify", json={"presentation": relabelled, "expected_audience": "landlord B"}
    )
    assert r.json()["valid"] is False and r.json()["reason"] == "bad_holder_proof"
    a = await client.post(
        "/passport/verify", json={"presentation": pres, "expected_audience": "landlord A"}
    )
    assert a.json()["valid"] is True  # the failed attempts at B did not use up A's challenge


async def test_a_challenge_expires_after_ten_minutes_and_is_pruned(
    client: httpx.AsyncClient, sandbox: Engine, service
) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import text

    ch = (await client.post("/passport/challenge", json={"audience": "slow verifier"})).json()
    shared = await _confirm(
        client, sandbox, await _issue_and_share(client, sandbox, "slow verifier", ch["nonce"])
    )
    pres = shared.json()["presentation"]
    prev = service.deps.now_fn
    service.deps.now_fn = lambda: prev() + timedelta(minutes=11)
    try:
        late = await client.post(
            "/passport/verify", json={"presentation": pres, "expected_audience": "slow verifier"}
        )
        assert late.json()["valid"] is False and late.json()["reason"] == "challenge_expired"
        await client.post(
            "/passport/challenge", json={"audience": "next"}
        )  # issuing prunes old rows
    finally:
        service.deps.now_fn = prev
    with sandbox.connect() as c:
        left = c.execute(
            text("SELECT count(*) FROM passport_challenges WHERE audience = 'slow verifier'")
        ).scalar_one()
    assert left == 0
