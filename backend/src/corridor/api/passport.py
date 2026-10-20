"""Credit Passport HTTP routes (P3-1, ADR 0015): issue, present, confirm, list, verify.

Issuing and presenting change state or send personal data to someone else, so they follow the same path
as every action: a proposal, a deterministic policy decision logged to the ledger, a confirmation that
mints a single use grant bound to the proposal hash (MFA when policy asks), and only then the executor.
``verify`` is a pure function of the presentation and the issuer's public key: it needs no login, no
database and no model, which is the point of a signed credential. It is a signed credential, not a
blockchain and not on-chain.
"""

import asyncio
import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from slowapi import Limiter
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from corridor.agent.proposals import build_passport_issue, build_passport_share, proposal_card
from corridor.agent.service import AgentService
from corridor.confirm import record_policy_denial
from corridor.contracts.action import ActionProposal
from corridor.contracts.api import (
    PassportChallengeRequest,
    PassportChallengeResponse,
    PassportConfirmRequest,
    PassportConfirmResponse,
    PassportListResponse,
    PassportPresentRequest,
    PassportProposalResponse,
    PassportVerifyRequest,
    PassportVerifyResponse,
)
from corridor.contracts.confirm import ConfirmationLevel
from corridor.contracts.ledger import LedgerActor, LedgerEventType
from corridor.contracts.policy import Decision
from corridor.contracts.tools import ToolResult
from corridor.executor import ExecutionRejected, Executor, PassportDone, required_level
from corridor.passport import credential
from corridor.passport.claims import STATEMENTS, preview_claim_ids
from corridor.policy import evaluate
from corridor.risk import assess as assess_risk
from corridor.risk import risk_view
from corridor.sandbox import vault
from corridor.sandbox.account_state import build_account_state
from corridor.sandbox.models import PASSPORT_CHALLENGE_MINUTES, Passport, PassportChallenge

PENDING_TTL_S = 600
MAX_LIVE_CHALLENGES = 5000


@dataclass
class Pending:
    user: str
    proposal: ActionProposal
    level: ConfirmationLevel
    card: dict[str, Any] = field(default_factory=dict)
    risk: dict[str, Any] | None = None
    at: float = field(default_factory=time.monotonic)


class PendingStore:
    """Proposals waiting for a confirmation. In memory and short lived on purpose: a restart drops
    them, which only means "ask again" (nothing executes without a fresh, matching confirmation)."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], Pending] = {}

    def put(self, user: str, phash: str, item: Pending) -> None:
        now = time.monotonic()
        self._items = {k: v for k, v in self._items.items() if now - v.at < PENDING_TTL_S}
        self._items[(user, phash)] = item

    def cards_for(self, user: str) -> list[dict[str, Any]]:
        now = time.monotonic()
        return [
            {
                "proposal_hash": h,
                "kind": "passport",
                "thread_id": None,
                "required_level": it.level.value,
                "card": it.card,
                "risk": it.risk,
            }
            for (u, h), it in self._items.items()
            if u == user and now - it.at < PENDING_TTL_S
        ]

    def pop(self, user: str, phash: str) -> Pending | None:
        item = self._items.pop((user, phash), None)
        if item is None or time.monotonic() - item.at >= PENDING_TTL_S:
            return None
        return item


_STORES: list[PendingStore] = []


def pending_for(user: str) -> list[dict[str, Any]]:
    """Pending passport proposals of one user, as the shared PendingProposal shape."""
    out: list[dict[str, Any]] = []
    for store in _STORES:
        out += store.cards_for(user)
    return out


def register(
    app: FastAPI, *, service: AgentService, limiter: Limiter, current_user: Any, now_fn: Any
) -> None:
    store = PendingStore()
    _STORES.clear()  # one app per process: a new app (tests build many) never sees an old app's proposals
    _STORES.append(store)
    cache: dict[str, Any] = {}

    def deps() -> Any:
        """The service's dependencies, resolved on first use (the service is built after the app)."""
        return service.deps

    def executor() -> Executor:
        if "executor" not in cache:
            d = deps()
            cache["executor"] = Executor(d.engine, d.policy, d.ledger, d.ablations, d.secrets_dir)
        return cache["executor"]  # type: ignore[no-any-return]

    def issuer_pub() -> str:
        if "pub" not in cache:
            cache["pub"] = credential.public_key_hex(
                vault.passport_signing_key_hex(deps().secrets_dir)
            )
        return cache["pub"]  # type: ignore[no-any-return]

    def decide(user: str, proposal: ActionProposal) -> dict[str, Any]:
        """Policy decision, logged with the proposal before anything is returned (INV-8)."""
        now: datetime = now_fn()
        trace = uuid.uuid4().hex
        phash = proposal.canonical_hash()
        with Session(deps().engine) as s, s.begin():
            state = build_account_state(s, user, now, deps().policy)
            decision = evaluate(proposal, state, deps().policy, now)
            risk_json = risk_view(assess_risk(proposal, state, deps().policy, now))
            for event, data in (
                (LedgerEventType.PROPOSAL, {"proposal_hash": phash, "action": proposal.action.value,
                                            "source_account": proposal.source_account_id.value,
                                            "passport_share": proposal.passport_share.model_dump(mode="json")
                                            if proposal.passport_share else None}),
                (LedgerEventType.POLICY_DECISION, {"proposal_hash": phash, "decision": decision.decision.value,
                                                   "rule_ids": list(decision.rule_ids),
                                                   "reasons": list(decision.reasons),
                                                   "rule_results": [list(r) for r in decision.rule_results],
                                                   "risk": risk_json}),
            ):  # fmt: skip
                deps().ledger.append(
                    s, trace_id=trace, actor=LedgerActor.AGENT, event_type=event, user_id=user,
                    session_id=None, data=data, policy_version_hash=decision.policy_version_hash, now=now,
                )  # fmt: skip
        if decision.decision is Decision.DENY or decision.decision is Decision.ALLOW:
            with Session(deps().engine) as s, s.begin():  # a denied attempt counts toward the lock
                reason = record_policy_denial(s, user, decision, now, deps().policy, ())
                if reason is not None:
                    deps().ledger.append(
                        s, trace_id=trace, actor=LedgerActor.AGENT,
                        event_type=LedgerEventType.SAFETY_LOCK, user_id=user, session_id=None,
                        data={"action": "locked", "reason": reason},
                        policy_version_hash=decision.policy_version_hash, now=now,
                    )  # fmt: skip
            raise HTTPException(
                status_code=409,
                detail={"reason": "policy_denied", "rule_ids": list(decision.rule_ids),
                        "reasons": list(decision.reasons)},
            )  # fmt: skip
        level = required_level(decision)
        card = proposal_card_for(proposal)
        store.put(
            user, phash,
            Pending(user, proposal, level, card, risk_json),
        )  # fmt: skip
        return {
            "proposal_hash": phash, "required_level": level.value,
            "rule_ids": list(decision.rule_ids), "reasons": list(decision.reasons),
            "card": proposal_card_for(proposal),
        }  # fmt: skip

    def proposal_card_for(p: ActionProposal) -> dict[str, Any]:
        from corridor.agent.proposals import ProposalBuild

        if p.passport_share is not None:
            display: dict[str, Any] = {
                "action": "share_passport",
                "shares_only": [STATEMENTS.get(c, c) for c in p.passport_share.claim_ids],
                "for": p.passport_share.audience,
                "hidden": "all other claims, all amounts and all account numbers",
            }
        else:
            display = {"action": "issue_passport", "valid_for": "90 days"}
        return proposal_card(ProposalBuild(p, display, None, ()))

    def balances_of(user: str) -> ToolResult:
        from corridor.agent.toolrun import run_read_tool

        return run_read_tool(deps().engine, user, "get_balances", {}, now_fn().date())

    @app.get("/passport", response_model=PassportListResponse)
    async def my_passports(user: str = Depends(current_user)) -> dict[str, Any]:
        with Session(deps().engine) as s:
            rows = s.scalars(
                select(Passport).where(Passport.user_id == user).order_by(Passport.issued_at.desc())
            ).all()
            return {
                "passports": [
                    {"passport_id": p.id, "claims": [
                        {
                            "id": c, "shows": STATEMENTS.get(c, c),
                            "facts": {
                                k: v
                                for d in p.disclosures
                                if d["claim"]["id"] == c
                                for k, v in d["claim"].items()
                                if isinstance(v, int) and not isinstance(v, bool)
                            },
                        }
                        for c in p.claim_ids
                    ],
                     "issued_at": p.issued_at.isoformat(), "expires_at": p.expires_at.isoformat()}
                    for p in rows
                ],
                "issuer_public_key": issuer_pub(),
            }  # fmt: skip

    @app.post("/passport/issue", response_model=PassportProposalResponse)
    @limiter.limit("10/minute")
    async def issue(request: Request, user: str = Depends(current_user)) -> dict[str, Any]:
        with Session(deps().engine) as ps:
            preview = tuple(preview_claim_ids(ps, user, now_fn().date()))
        build = build_passport_issue(
            user_id=user, balances=balances_of(user), claim_preview=preview
        )
        return await asyncio.to_thread(decide, user, build.proposal)

    @app.post("/passport/present", response_model=PassportProposalResponse)
    @limiter.limit("10/minute")
    async def present(
        request: Request, body: PassportPresentRequest, user: str = Depends(current_user)
    ) -> dict[str, Any]:
        if (
            body.challenge is not None
        ):  # the verifier's challenge must be live and for this audience
            with Session(deps().engine) as s:
                ch = s.get(PassportChallenge, body.challenge)
                now: datetime = now_fn()
                if (
                    ch is None
                    or ch.used_at is not None
                    or ch.audience != body.audience[:80]
                    or now - ch.issued_at > timedelta(minutes=PASSPORT_CHALLENGE_MINUTES)
                ):
                    raise HTTPException(status_code=409, detail={"reason": "challenge_invalid"})
        build = build_passport_share(
            user_id=user, balances=balances_of(user), passport_id=body.passport_id,
            claim_ids=tuple(body.claims), audience=body.audience, statements=STATEMENTS,
            nonce=body.challenge,
        )  # fmt: skip
        return await asyncio.to_thread(decide, user, build.proposal)

    @app.post("/passport/confirm", response_model=PassportConfirmResponse)
    @limiter.limit("20/minute")
    async def confirm(
        request: Request, body: PassportConfirmRequest, user: str = Depends(current_user)
    ) -> dict[str, Any]:
        item = store.pop(user, body.proposal_hash)
        if item is None:
            raise HTTPException(status_code=409, detail="nothing is awaiting confirmation")
        now: datetime = now_fn()
        trace = uuid.uuid4().hex

        def run() -> PassportDone | dict[str, Any]:
            with Session(deps().engine) as s, s.begin():
                outcome = service.confirmations.confirm(
                    s, user_id=user, proposal_hash=body.proposal_hash, required=item.level,
                    totp=body.totp, now=now, trace_id=trace, session_id=None,
                )  # fmt: skip
            if not outcome.ok or outcome.grant is None:
                store.put(user, body.proposal_hash, item)  # the user may retry with the right code
                return {"refused": outcome.reason, "locked": outcome.locked,
                        "attempts_left": outcome.attempts_left}  # fmt: skip
            res = executor().execute(
                item.proposal, outcome.grant.grant_id, user_id=user, now=now, trace_id=trace,
            )  # fmt: skip
            assert isinstance(res, PassportDone)
            return res

        try:
            result = await asyncio.to_thread(run)
        except ExecutionRejected as exc:
            raise HTTPException(status_code=409, detail={"reason": exc.reason}) from exc
        if isinstance(result, dict):
            raise HTTPException(status_code=403, detail=result)
        return {
            "kind": result.kind, "passport_id": result.passport_id, "claims": list(result.claim_ids),
            "expires_at": result.expires_at, "audience": result.audience,
            "presentation": result.presentation,
        }  # fmt: skip

    def prune(s: Session, now: datetime) -> None:
        """Delete challenges older than the window: an expired challenge can never be answered."""
        s.execute(
            delete(PassportChallenge).where(
                PassportChallenge.issued_at < now - timedelta(minutes=PASSPORT_CHALLENGE_MINUTES)
            )
        )

    @app.post("/passport/challenge", response_model=PassportChallengeResponse)
    @limiter.limit("30/minute")
    async def challenge(request: Request, body: PassportChallengeRequest) -> dict[str, Any]:
        """Public: a verifier names itself and gets a one time nonce, valid for 10 minutes. The holder's
        presentation must answer it (ADR 0015): a presentation made for another verifier, an old one or
        a replayed one is refused. Expired challenges are deleted here."""
        now: datetime = now_fn()
        nonce = secrets.token_hex(16)
        with Session(deps().engine) as s, s.begin():
            prune(s, now)
            live = s.scalar(select(func.count()).select_from(PassportChallenge)) or 0
            if live >= MAX_LIVE_CHALLENGES:
                raise HTTPException(status_code=503, detail="too many open challenges")
            s.add(
                PassportChallenge(
                    nonce=nonce, audience=body.audience[:80], issued_at=now, issued_by="verifier"
                )
            )
        return {
            "nonce": nonce, "audience": body.audience[:80],
            "expires_at": (now + timedelta(minutes=PASSPORT_CHALLENGE_MINUTES)).isoformat(),
        }  # fmt: skip

    @app.post("/passport/verify", response_model=PassportVerifyResponse)
    @limiter.limit("60/minute")
    async def verify(request: Request, body: PassportVerifyRequest) -> dict[str, Any]:
        """Public: anyone can check a presentation. Pure code, plus one write: the nonce of an accepted
        presentation is recorded, so the same presentation is accepted once per verifier database."""
        now: datetime = now_fn()
        audience = body.expected_audience[:80]

        def consume(nonce: str) -> str | None:
            """The nonce must be a live challenge this verifier issued for this audience, used once."""
            if len(nonce) > 64:
                return "challenge_unknown"
            with Session(deps().engine) as s, s.begin():
                row = s.get(PassportChallenge, nonce, with_for_update=True)
                if row is None:
                    return "challenge_unknown"
                if row.used_at is not None:
                    return "nonce_reused"
                if row.audience != audience:
                    return "wrong_audience"
                if now - row.issued_at > timedelta(minutes=PASSPORT_CHALLENGE_MINUTES):
                    return "challenge_expired"
                row.used_at = now
                return None

        v = await asyncio.to_thread(
            credential.verify, body.presentation, trusted_public_key_hex=issuer_pub(), now=now,
            expected_audience=body.expected_audience, consume_nonce=consume,
        )  # fmt: skip
        return {
            "valid": v.valid, "reason": v.reason, "audience": v.audience, "holder": v.holder,
            "expires_at": v.expires_at,
            "claims": [{**c, "shows": STATEMENTS.get(c["id"], c["id"])} for c in v.claims],
        }  # fmt: skip


_ = hashlib  # keep the import used for type checkers
