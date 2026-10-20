"""The HTTP surface, P1 subset of docs/spec/modules.md 5.16.

POST /chat streams AgentEvents as server sent events; POST /chat/{thread}/confirm resumes a paused
graph with the proposal hash; POST /chat/{thread}/cancel discards it; GET /accounts, /payees and
/transfers read the user's own data through the read only tools. Safety properties:
- the user id comes only from the bearer token;
- a thread belonging to another user is a 404, never revealed;
- request bodies are size limited, requests are rate limited, CORS allows one configured origin;
- the dev chat page is served only when APP_ENV=dev and only to loopback clients.
"""

import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import structlog
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from corridor.agent.service import AgentService, NoPendingConfirmation, ThreadNotFound
from corridor.agent.toolrun import ToolError, run_read_tool
from corridor.api.auth import TokenAuth, current_user, officer_id_for
from corridor.config import AppEnv, Settings
from corridor.confirm import current_code
from corridor.contracts.api import (
    AccountsResponse,
    ChatRequest,
    ClockResponse,
    ConfirmRequest,
    ContributionRespondRequest,
    ContributionsResponse,
    ContributionView,
    OfficerVerifyPayeeRequest,
    OfficerVerifyPayeeResponse,
    PayeesResponse,
    PendingProposalsResponse,
    RouteCompareRequest,
    RouteComparisonResponse,
    TuitionPlanResponse,
    UnlockRequest,
    VerificationRequestResponse,
)
from corridor.contracts.events import AgentEvent
from corridor.contracts.ledger import LedgerActor, LedgerEventType
from corridor.contracts.money import CurrencyCode
from corridor.executor import (
    VerificationRefused,
    VerificationRequestRefused,
    request_verification,
    verify_payee,
)
from corridor.formatting import format_minor
from corridor.ledger import Ledger, proof_for, verify_chain
from corridor.reader import UploadError, read_upload
from corridor.sandbox.models import (
    ContributionRequest,
    FamilyLink,
    Payee,
    User,
    VerificationRequest,
)

log = structlog.get_logger("corridor.api")
MAX_BODY_BYTES = 32 * 1024
UPLOAD_BODY_BYTES = 10 * 1024 * 1024 + 4096
DEV_PAGE = Path(__file__).parent / "dev_chat.html"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class BodySizeLimit:
    """ASGI middleware: reject request bodies over ``limit`` bytes with 413 (also when chunked)."""

    def __init__(self, app: ASGIApp, limit: int = MAX_BODY_BYTES) -> None:
        self.app, self.default_limit = app, limit
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        self_limit = (
            UPLOAD_BODY_BYTES if scope.get("path") == "/attachments" else self.default_limit
        )
        limit = self_limit
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await JSONResponse({"detail": "request too large"}, status_code=413)(
                scope, receive, send
            )
            return
        seen = 0

        async def limited() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise HTTPException(status_code=413, detail="request too large")
            return message

        await self.app(scope, limited, send)


def sse(events: AsyncIterator[AgentEvent], thread_id: str | None = None) -> StreamingResponse:
    async def body() -> AsyncIterator[str]:
        async for ev in events:
            yield f"event: {ev.type.value}\ndata: {ev.model_dump_json()}\n\n"

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            **({"X-Thread-Id": thread_id} if thread_id else {}),
        },
    )


def create_app(
    *, settings: Settings, service: AgentService, engine: Engine, auth: TokenAuth, today: Any,
    compare: Any = None, officer_token: dict[str, str] | str | None = None, clock: Any = None,
) -> FastAPI:  # fmt: skip
    """Build the FastAPI app around an already constructed service (tests inject fakes)."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield

    app = FastAPI(title="Corridor AI (sandbox)", lifespan=lifespan)
    app.state.service, app.state.auth, app.state.engine, app.state.settings = (
        service,
        auth,
        engine,
        settings,
    )

    def rate_key(request: Request) -> str:
        """Limit per authenticated user (a hash of the token), else per address."""
        header = request.headers.get("authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        user = request.app.state.auth.user_for(token) if token else None
        if (
            user is not None
        ):  # only a valid token earns its own bucket; guesses share the address bucket
            return "usr:" + hashlib.sha256(user.encode()).hexdigest()[:16]
        header = settings.trusted_proxy_header
        if header:  # a proxy we run sets this header; without it the address of the caller is used
            forwarded = request.headers.get(header, "").split(",")[0].strip()
            if forwarded:
                return forwarded[:64]
        return get_remote_address(request)

    limiter = Limiter(key_func=rate_key, default_limits=["120/minute"])
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
    app.add_middleware(SlowAPIMiddleware)  # applies the default limit to every route
    app.add_middleware(BodySizeLimit)
    app.add_middleware(
        CORSMiddleware, allow_origins=[settings.frontend_origin], allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )  # fmt: skip

    def svc(request: Request) -> AgentService:
        return request.app.state.service  # type: ignore[no-any-return]

    if (
        compare is not None
    ):  # side by side demo, dev and demo only (the baseline is never behind /chat)
        from corridor.api import compare as compare_module

        compare_module.register(
            app, runner=compare, settings=settings, current_user=current_user, limiter=limiter
        )

    from corridor.api import passport as passport_routes

    passport_routes.register(
        app,
        service=service,
        limiter=limiter,
        current_user=current_user,
        now_fn=lambda: service.deps.now_fn(),
    )

    from corridor.api import officer as officer_routes

    officer_routes.register(
        app, engine=engine, officer_token=officer_token, now_fn=lambda: service.deps.now_fn()
    )

    @app.post("/officer/payees/{payee_id}/verify", response_model=OfficerVerifyPayeeResponse)
    async def verify_payee_route(
        payee_id: str, body: OfficerVerifyPayeeRequest, request: Request
    ) -> OfficerVerifyPayeeResponse:
        """A bank officer verifies a payee after a Confirmation of Payee match (ADR 0012).

        Customers cannot reach this: it needs the officer token, which no customer token equals. It is not
        a tool of any model and not an action the agent can propose (INV-1)."""
        officer = officer_id_for(request, officer_token)
        try:
            done = verify_payee(
                engine, service.deps.policy, officer_id=officer, payee_id=payee_id,
                method=body.method, reference=body.reference, now=service.deps.now_fn(), trace_id=uuid.uuid4().hex,
            )  # fmt: skip
        except VerificationRefused as exc:
            raise HTTPException(status_code=409, detail=exc.reason) from exc
        return OfficerVerifyPayeeResponse(
            verified=True, payee_id=done.payee_id, officer_id=done.officer_id
        )

    @app.post("/chat")
    @limiter.limit("30/minute")
    async def chat(
        request: Request, body: ChatRequest, user: str = Depends(current_user)
    ) -> StreamingResponse:
        s = svc(request)
        if body.thread_id is not None:
            try:
                await s.state(user, body.thread_id)
            except ThreadNotFound:
                if await s.thread_exists(body.thread_id):  # someone else's thread: never reveal it
                    raise HTTPException(status_code=404, detail="thread not found") from None
        thread = body.thread_id or uuid.uuid4().hex
        events = s.chat(
            user,
            body.message,
            thread,
            [a.model_dump() for a in body.attachments],
            body.forwarded,
            structured=body.action.model_dump(mode="json") if body.action else None,
        )
        return sse(events, thread)

    @app.post("/attachments")
    @limiter.limit("10/minute")
    async def attachments(request: Request, user: str = Depends(current_user)) -> dict[str, Any]:
        """Upload an image or PDF (raw body, at most 10 MB). The file type comes from magic bytes.

        The quarantined reader extracts typed fields; the response carries only a typed summary and
        the injection signals, never the document's own words."""
        data = await request.body()
        s = svc(request)
        try:
            result = await read_upload(
                s.deps.gateway, engine, user, data, s.deps.now_fn(), s.deps.secrets_dir
            )
        except UploadError as exc:
            status = {"too_large": 413, "unsupported_type": 415}.get(exc.code, 400)
            raise HTTPException(status_code=status, detail=exc.code) from None
        except Exception as exc:  # the reader failed: fail closed, nothing was stored (INV-10)
            log.error("attachment_failed", error=type(exc).__name__)
            raise HTTPException(status_code=502, detail="reader_unavailable") from None
        try:  # the extraction is part of the audit trail from the moment it happens (INV-8)
            with Session(engine) as ls, ls.begin():
                Ledger().append(
                    ls, trace_id=uuid.uuid4().hex, actor=LedgerActor.SYSTEM,
                    event_type=LedgerEventType.READER_EXTRACTION, user_id=user, session_id=None,
                    data={
                        "doc_hash": result.doc_hash, "kind": result.kind,
                        "injection_suspected": result.injection_suspected,
                        "signals": list(result.signals), "via": "upload",
                    },
                    policy_version_hash=s.deps.policy.version_hash, now=s.deps.now_fn(),
                )  # fmt: skip
        except (
            Exception
        ) as exc:  # no ledger entry, no doc id handed back (the stored row is harmless)
            log.error("attachment_ledger_failed", error=type(exc).__name__)
            raise HTTPException(status_code=502, detail="ledger_unavailable") from None
        return {
            "doc_id": result.doc_hash, "kind": result.kind,
            "injection_suspected": result.injection_suspected, "signals": list(result.signals),
            "summary": result.summary,
        }  # fmt: skip

    @app.post("/chat/{thread_id}/confirm")
    @limiter.limit("30/minute")
    async def confirm(
        thread_id: str, request: Request, body: ConfirmRequest, user: str = Depends(current_user)
    ) -> StreamingResponse:
        s = svc(request)
        try:
            await s.state(user, thread_id)
            snap_events = s.confirm(user, thread_id, body.proposal_hash, body.totp)
            first = await snap_events.__anext__()
        except StopAsyncIteration:
            raise HTTPException(status_code=409, detail="nothing happened") from None
        except ThreadNotFound:
            raise HTTPException(status_code=404, detail="thread not found") from None
        except NoPendingConfirmation:
            raise HTTPException(
                status_code=409, detail="nothing is awaiting confirmation"
            ) from None

        async def rest() -> AsyncIterator[AgentEvent]:
            yield first
            async for ev in snap_events:
                yield ev

        return sse(rest(), thread_id)

    @app.post("/chat/{thread_id}/cancel")
    @limiter.limit("30/minute")
    async def cancel(
        thread_id: str, request: Request, user: str = Depends(current_user)
    ) -> StreamingResponse:
        s = svc(request)
        try:
            await s.state(user, thread_id)
        except ThreadNotFound:
            raise HTTPException(status_code=404, detail="thread not found") from None
        return sse(s.cancel(user, thread_id), thread_id)

    def read(user: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        return run_read_tool(engine, user, tool, args, today(), now=service.deps.now_fn()).data

    @app.get("/accounts", response_model=AccountsResponse)
    async def accounts(user: str = Depends(current_user)) -> dict[str, Any]:
        return read(user, "get_balances", {})

    @app.get("/payees", response_model=PayeesResponse)
    async def payees(user: str = Depends(current_user)) -> dict[str, Any]:
        """Saved payees. A payee from a document shows the fixed label, never its raw name (INV-3).
        ``hold_until`` and ``verification`` come from the database, not from the read tool."""
        data = read(user, "get_payees", {})
        now = service.deps.now_fn()
        hours = service.deps.policy.payee_cooloff.hours
        with Session(engine) as s:
            rows = {p.id: p for p in s.scalars(select(Payee).where(Payee.user_id == user))}
            created = {pid: r.created_at for pid, r in rows.items()}
            latest: dict[str, str] = {}
            for r in s.scalars(
                select(VerificationRequest)
                .where(VerificationRequest.user_id == user)
                .order_by(VerificationRequest.created_at)
            ):
                latest[r.payee_id] = r.status
        for p in data.get("payees", []):
            row = rows.get(p["payee_id"])
            if row is not None and row.provenance == "user_typed" and row.added_via != "document":
                # the name the user typed is shown as typed; the planner still sees the fixed label
                p["display_name"], p["name_untrusted"] = row.display_name, False
            at = created.get(p["payee_id"])
            until = at + timedelta(hours=hours) if at is not None else None
            released = bool(p.get("verified")) and p.get("provenance") == "user_typed"
            p["hold_until"] = (
                until.isoformat() if until is not None and now < until and not released else None
            )
            p["verification"] = (
                "verified" if p.get("verified") else latest.get(p["payee_id"], "none")
            )
        return data

    @app.post("/payees/{payee_id}/verification-request", response_model=VerificationRequestResponse)
    @limiter.limit("10/minute")
    async def request_payee_verification(
        request: Request, payee_id: str, user: str = Depends(current_user)
    ) -> VerificationRequestResponse:
        """Ask the bank to verify one of your payees. It only adds it to the officer queue: the customer
        cannot verify anything, and an officer needs a Confirmation of Payee name match (ADR 0012, 0018)."""
        try:
            done = request_verification(
                engine, service.deps.policy, user_id=user, payee_id=payee_id,
                now=service.deps.now_fn(), trace_id=uuid.uuid4().hex,
            )  # fmt: skip
        except VerificationRequestRefused as exc:
            code = 404 if exc.reason == "payee_not_found" else 409
            raise HTTPException(status_code=code, detail=exc.reason) from exc
        return VerificationRequestResponse(
            request_id=done.request_id, payee_id=done.payee_id, status="pending"
        )

    def contribution_views(s: Session, rows: Any) -> list[ContributionView]:
        users = {u.id: u for u in s.scalars(select(User)).all()}
        names = {k: v.display_name for k, v in users.items()}
        links = {
            (fl.user_id, fl.contributor_user_id): fl.relation
            for fl in s.scalars(select(FamilyLink)).all()
        }
        return [
            ContributionView(
                request_id=r.id, requester=names.get(r.requester_user_id, "?"),
                contributor=names.get(r.contributor_user_id, "?"),
                requester_region=getattr(users.get(r.requester_user_id), "home_region", None),
                contributor_region=getattr(users.get(r.contributor_user_id), "home_region", None),
                contributor_relation=links.get((r.requester_user_id, r.contributor_user_id)),
                amount=format_minor(r.amount_minor, CurrencyCode(r.currency)),
                currency=CurrencyCode(r.currency), invoice_id=r.invoice_id, status=r.status,
                created_at=r.created_at.isoformat(),
            )
            for r in rows
        ]  # fmt: skip

    @app.get("/contributions", response_model=ContributionsResponse)
    async def contributions(user: str = Depends(current_user)) -> ContributionsResponse:
        """Requests for family money: those addressed to you (incoming) and those you made (outgoing)."""
        with Session(engine) as s:
            mine = [
                s.scalars(
                    select(ContributionRequest)
                    .where(col == user)
                    .order_by(ContributionRequest.created_at.desc())
                    .limit(50)
                ).all()
                for col in (
                    ContributionRequest.contributor_user_id,
                    ContributionRequest.requester_user_id,
                )
            ]
            return ContributionsResponse(
                incoming=contribution_views(s, mine[0]), outgoing=contribution_views(s, mine[1])
            )

    @app.post("/contributions/{request_id}/respond", response_model=ContributionView)
    @limiter.limit("20/minute")
    async def respond_to_contribution(
        request: Request,
        request_id: str,
        body: ContributionRespondRequest,
        user: str = Depends(current_user),
    ) -> ContributionView:
        """The contributor accepts or declines a request addressed to them. It records the answer and
        moves no money: the contributor sends the money from their own account (INV-1). Ledgered (INV-8)."""
        now = service.deps.now_fn()
        with Session(engine) as s, s.begin():
            row = s.scalars(
                select(ContributionRequest)
                .where(
                    ContributionRequest.id == request_id,
                    ContributionRequest.contributor_user_id == user,  # never someone else's request
                )
                .with_for_update()
            ).first()
            if row is None:
                raise HTTPException(status_code=404, detail="request not found")
            if row.status != "pending":
                raise HTTPException(status_code=409, detail="already answered")
            row.status = "accepted" if body.accept else "declined"
            service.deps.ledger.append(
                s, trace_id=uuid.uuid4().hex, actor=LedgerActor.USER,
                event_type=LedgerEventType.CONTRIBUTION_RESPONDED, user_id=user, session_id=None,
                data={"action": "respond_contribution", "request_id": row.id, "status": row.status,
                      "requester": row.requester_user_id, "amount_minor": row.amount_minor,
                      "currency": row.currency},
                policy_version_hash=service.deps.policy.version_hash, now=now,
            )  # fmt: skip
            return contribution_views(s, [row])[0]

    @app.get("/clock", response_model=ClockResponse)
    async def sandbox_clock() -> ClockResponse:
        """The sandbox date. ``demo`` is true when it is fixed to the documented demo date: the apps show it."""
        now = service.deps.now_fn()
        demo = bool(clock is not None and clock.demo)
        return ClockResponse(
            demo=demo, now=now.isoformat(), date=today().isoformat(),
            label=clock.label if demo else None,
            sandbox=settings.app_env in (AppEnv.DEV, AppEnv.DEMO),
        )  # fmt: skip

    @app.get("/proposals/pending", response_model=PendingProposalsResponse)
    async def pending(request: Request, user: str = Depends(current_user)) -> dict[str, Any]:
        """The caller's proposals waiting for a confirmation: chat threads and passport requests."""
        mine = list(svc(request).pending_proposals(user))
        mine += passport_routes.pending_for(user)
        return {"proposals": mine}

    @app.post(
        "/routes/compare", response_model=RouteComparisonResponse, response_model_exclude_none=True
    )
    @limiter.limit("30/minute")
    async def routes_compare(
        request: Request, body: RouteCompareRequest, user: str = Depends(current_user)
    ) -> dict[str, Any]:
        """Every feasible route for an amount, cheapest first (exact enumeration, ADR 0013)."""
        args: dict[str, Any] = {
            "from_currency": body.from_currency.value,
            "to_currency": body.to_currency.value,
            "amount_minor": body.amount_minor,
        }
        if body.deadline:
            args["deadline"] = body.deadline
        try:
            return read(user, "compare_routes", args)
        except ToolError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/tuition/plan", response_model=TuitionPlanResponse, response_model_exclude_none=True)
    @limiter.limit("20/minute")
    async def tuition_plan(
        request: Request,
        invoice_id: str = Query(min_length=1, max_length=64),
        user: str = Depends(current_user),
    ) -> dict[str, Any]:
        """The cheapest way to fund what is owed on one of the caller's invoices (CP-SAT, ADR 0014)."""
        return read(user, "plan_tuition_funding", {"invoice_id": invoice_id})

    @app.get("/transfers")
    async def transfers(user: str = Depends(current_user)) -> dict[str, Any]:
        return read(user, "get_history", {"days": 366, "limit": 100})

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    def dev_only(request: Request, envs: tuple[AppEnv, ...] = (AppEnv.DEV,)) -> None:
        """404 unless APP_ENV is in ``envs`` (dev), the client is loopback, the Host header is loopback
        too (refuses DNS rebinding) and no proxy header is present."""
        host = request.client.host if request.client else ""
        served_as = request.headers.get("host", "").rsplit(":", 1)[0].strip("[]")
        proxied = any(
            h in request.headers
            for h in ("x-forwarded-for", "x-forwarded-host", "x-real-ip", "forwarded", "via")
        )
        if (
            settings.app_env not in envs
            or host not in LOOPBACK
            or served_as not in LOOPBACK
            or proxied
        ):
            raise HTTPException(status_code=404)

    @app.get("/dev/totp")
    async def dev_totp(request: Request, user: str = Depends(current_user)) -> dict[str, Any]:
        """DEV ONLY: the current authenticator code of the token's own persona, for testing."""
        dev_only(request)
        now = svc(request).deps.now_fn()
        return {
            "user_id": user,
            "code": current_code(user, now, svc(request).deps.secrets_dir),
            "valid_for_seconds": 30 - int(now.timestamp()) % 30,
        }

    @app.get("/demo/authenticator")
    async def demo_authenticator(
        request: Request, user: str = Depends(current_user)
    ) -> dict[str, Any]:
        """SANDBOX ONLY (APP_ENV dev or demo, loopback only): the current code of the token's own persona, so a
        recording can show the authenticator app. A stand-in for the user's phone. The seed and the code are
        never logged; in any other environment this route does not exist (404)."""
        dev_only(
            request, (AppEnv.DEV, AppEnv.DEMO)
        )  # same loopback, Host and no-proxy checks as /dev/totp
        now = svc(request).deps.now_fn()
        return {
            "code": current_code(user, now, svc(request).deps.secrets_dir),
            "valid_for_seconds": 30 - int(now.timestamp()) % 30,
        }

    @app.post("/unlock")
    @limiter.limit("10/minute")
    async def unlock(
        request: Request, body: UnlockRequest, user: str = Depends(current_user)
    ) -> dict[str, Any]:
        """Lift the safety lock with a valid authenticator code (MFA re-verification, INV-11)."""
        outcome = await svc(request).unlock(user, body.totp)
        return {"unlocked": outcome.ok, "reason": outcome.reason}

    def ledger_rows(user: str, limit: int, before: int | None) -> list[dict[str, Any]]:
        """The caller's own ledger entries (an operator view arrives with the console in P3)."""
        with Session(engine) as s:
            rows = s.execute(
                text(
                    "SELECT seq, ts, trace_id, actor, event_type, payload_canonical_json, "
                    "payload_hash, policy_version_hash, prev_hash, entry_hash FROM ledger_entries "
                    "WHERE payload_canonical_json::jsonb ->> 'user_id' = :u "
                    "AND (CAST(:b AS BIGINT) IS NULL OR seq < :b) ORDER BY seq DESC LIMIT :n"
                ),
                {"u": user, "b": before, "n": limit},
            ).all()
        return [
            {
                "seq": r.seq, "ts": r.ts.isoformat(), "trace_id": r.trace_id, "actor": r.actor,
                "event_type": r.event_type, "payload": json.loads(r.payload_canonical_json),
                "payload_hash": r.payload_hash, "policy_version_hash": r.policy_version_hash,
                "prev_hash": r.prev_hash, "entry_hash": r.entry_hash,
            }
            for r in rows
        ]  # fmt: skip

    @app.get("/ledger")
    async def ledger(
        limit: int = Query(50, ge=1, le=200),
        before: int | None = Query(None, ge=1),
        user: str = Depends(current_user),
    ) -> dict[str, Any]:
        return {"entries": ledger_rows(user, limit, before)}

    @app.get("/ledger/verify")
    @limiter.limit("6/minute")
    async def ledger_verify(request: Request, user: str = Depends(current_user)) -> dict[str, Any]:
        """Recompute the whole chain. Reveals no content, only whether it is intact."""
        with Session(engine) as s:
            result = verify_chain(s)
        return {
            "valid": result.valid, "entries_checked": result.entries_checked,
            "first_broken_seq": result.first_broken_seq, "reason": result.reason,
        }  # fmt: skip

    @app.get("/ledger/proof/{seq}")
    @limiter.limit("30/minute")
    async def ledger_proof(
        request: Request, seq: int, user: str = Depends(current_user)
    ) -> dict[str, Any]:
        """A Merkle inclusion proof for one of the caller's own entries."""
        with Session(engine) as s:
            mine = s.execute(
                text(
                    "SELECT 1 FROM ledger_entries WHERE seq = :s "
                    "AND payload_canonical_json::jsonb ->> 'user_id' = :u"
                ),
                {"s": seq, "u": user},
            ).first()
            proof = proof_for(s, seq) if mine else None
        if proof is None:
            raise HTTPException(status_code=404, detail="entry not found")
        return proof

    @app.get("/dev/chat", response_class=HTMLResponse)
    async def dev_chat(request: Request) -> HTMLResponse:
        dev_only(request)
        tokens = json.dumps(auth.tokens).replace("<", "\\u003c")
        return HTMLResponse(
            DEV_PAGE.read_text(encoding="utf-8").replace("__DEMO_TOKENS__", tokens),
            headers={
                "Cache-Control": "no-store",
                "X-Frame-Options": "DENY",
                "Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'",
            },
        )

    return app
