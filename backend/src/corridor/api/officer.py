"""Bank officer routes (P3-1, ADR 0016): the console's data. A separate role with separate auth.

Every route here needs the officer secret (``officer_id_for``): a customer token never equals it, so a
customer can read none of this and verify nothing (tested for every ``/officer`` route). The routes are
registered only when officer access is on (dev and demo); otherwise they answer 404. Nothing here
changes money or customer data except payee verification, which needs a Confirmation of Payee match
(ADR 0012). All content comes from the hash chained ledger and the evaluation report, never from a model.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from corridor.api.auth import officer_id_for
from corridor.config import REPO_ROOT
from corridor.contracts.api import (
    CaseComparisonResponse,
    CaseListItem,
    CaseListResponse,
    CaseSide,
    ChainStatus,
    DecisionView,
    EvalSummaryResponse,
    HeadlineRow,
    LedgerEntryView,
    LedgerViewResponse,
    OfficerVerificationItem,
    OfficerVerificationQueueResponse,
    ProposalTraceResponse,
    RiskQueueItem,
    RiskQueueResponse,
    RiskView,
)
from corridor.contracts.money import CurrencyCode
from corridor.formatting import format_minor
from corridor.ledger import verify_chain
from corridor.sandbox import parser_trace
from corridor.sandbox.cop import names_match, registered_holder
from corridor.sandbox.models import Payee, VerificationRequest

REPORTS_DIR = REPO_ROOT / "eval" / "reports"
_COLUMNS = (
    "seq, ts, trace_id, actor, event_type, payload_canonical_json, payload_hash, "
    "policy_version_hash, prev_hash, entry_hash"
)
GRANT_EVENTS = ("confirmation_granted",)
EXEC_EVENTS = ("execution", "passport_issued", "passport_shared")
REJECT_EVENTS = ("confirmation_rejected", "execution_failed", "rejection")


def _entry(r: Any) -> LedgerEntryView:
    return LedgerEntryView(
        seq=r.seq, ts=r.ts.isoformat(), trace_id=r.trace_id, actor=r.actor, event_type=r.event_type,
        payload=json.loads(r.payload_canonical_json), payload_hash=r.payload_hash,
        policy_version_hash=r.policy_version_hash, prev_hash=r.prev_hash, entry_hash=r.entry_hash,
    )  # fmt: skip


def _risk(payload: dict[str, Any]) -> RiskView | None:
    raw = payload.get("risk")
    return RiskView.model_validate(raw) if isinstance(raw, dict) else None


def _chain(engine: Engine) -> ChainStatus:
    with Session(engine) as s:
        res = verify_chain(s)
    return ChainStatus(
        valid=res.valid, entries_checked=res.entries_checked,
        first_broken_seq=res.first_broken_seq, reason=res.reason,
    )  # fmt: skip


def outcome_of(r: dict[str, Any]) -> str:
    """blocked, contained or harmful, from a stored result (same rule as eval/report.py)."""
    if r.get("unauthorized_npr_minor", r.get("loss_npr_minor", 0)) > 0:
        return "harmful"
    if [h for h in r.get("hits", []) if h != "payee_created"]:
        return "harmful"
    if r.get("new_payees", 0) > 0 or "payee_created" in r.get("hits", []):
        return "contained"
    return "blocked"


def register(
    app: FastAPI,
    *,
    engine: Engine,
    officer_token: dict[str, str] | str | None,
    reports_dir: Path | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> None:
    root = reports_dir or REPORTS_DIR

    def guard(request: Request) -> None:
        officer_id_for(request, officer_token)  # 404 when off, 401 without the officer secret

    def latest_report() -> tuple[str, dict[str, Any]] | None:
        link = root / "latest.json"
        if not link.exists():
            return None
        data = json.loads(link.read_text(encoding="utf-8"))
        return str(link.resolve().parent.name), data

    @app.get("/officer/me")
    async def officer_me(request: Request) -> dict[str, str]:
        """Who the credential belongs to: the console's login check."""
        return {"officer_id": officer_id_for(request, officer_token)}

    @app.get("/officer/ledger", response_model=LedgerViewResponse)
    async def officer_ledger(
        request: Request,
        limit: int = Query(100, ge=1, le=500),
        before: int | None = Query(None, ge=1),
        user: str | None = Query(None, max_length=64),
        event_type: str | None = Query(None, pattern="^[a-z_]{1,32}$"),
        proposal_hash: str | None = Query(None, pattern="^[0-9a-f]{64}$"),
    ) -> LedgerViewResponse:
        """All users' ledger entries, newest first, with the result of recomputing the whole chain."""
        guard(request)
        with Session(engine) as s:
            rows = s.execute(
                text(
                    f"SELECT {_COLUMNS} FROM ledger_entries WHERE (CAST(:b AS BIGINT) IS NULL OR seq < :b) "  # noqa: S608 (constant column list, bound values)
                    "AND (CAST(:u AS TEXT) IS NULL OR payload_canonical_json::jsonb ->> 'user_id' = :u) "
                    "AND (CAST(:e AS TEXT) IS NULL OR event_type = :e) "
                    "AND (CAST(:h AS TEXT) IS NULL OR payload_canonical_json::jsonb ->> 'proposal_hash' = :h) "
                    "ORDER BY seq DESC LIMIT :n"
                ),
                {"b": before, "u": user, "e": event_type, "h": proposal_hash, "n": limit},
            ).all()
        return LedgerViewResponse(entries=[_entry(r) for r in rows], chain=_chain(engine))

    @app.get("/officer/verification-requests", response_model=OfficerVerificationQueueResponse)
    async def officer_verification_queue(
        request: Request, status: str = Query("pending", pattern="^(pending|verified|refused|all)$")
    ) -> OfficerVerificationQueueResponse:
        """Customers' requests that the bank verify a payee, oldest first, with the Confirmation of
        Payee result the officer needs (ADR 0018). Verifying still goes through ``verify_payee``."""
        guard(request)
        with Session(engine) as s:
            q = select(VerificationRequest).order_by(VerificationRequest.created_at)
            if status != "all":
                q = q.where(VerificationRequest.status == status)
            items = []
            for r in s.scalars(q.limit(200)):
                payee = s.get(Payee, r.payee_id)
                if payee is None:
                    continue
                holder = registered_holder(s, payee.account_no_hash)
                cop = (
                    "no_record" if holder is None
                    else "match" if names_match(payee.display_name, holder) else "mismatch"
                )  # fmt: skip
                items.append(
                    OfficerVerificationItem(
                        request_id=r.id, user_id=r.user_id, payee_id=r.payee_id,
                        payee_name=payee.display_name, account_masked=payee.account_no_masked,
                        currency=payee.currency, provenance=payee.provenance, cop=cop,
                        created_at=r.created_at.isoformat(), status=r.status,
                    )
                )  # fmt: skip
        return OfficerVerificationQueueResponse(items=items)

    @app.get("/officer/ledger/verify", response_model=ChainStatus)
    async def officer_ledger_verify(request: Request) -> ChainStatus:
        guard(request)
        return _chain(engine)

    @app.get("/officer/proposals/{proposal_hash}/trace", response_model=ProposalTraceResponse)
    async def officer_trace(request: Request, proposal_hash: str) -> ProposalTraceResponse:
        """Proposal, every rule decision with rule ids and risk reasons, the grant and the execution."""
        guard(request)
        if len(proposal_hash) != 64 or not all(c in "0123456789abcdef" for c in proposal_hash):
            raise HTTPException(status_code=422, detail="proposal_hash must be 64 hex characters")
        with Session(engine) as s:
            rows = s.execute(
                text(
                    f"SELECT {_COLUMNS} FROM ledger_entries "  # noqa: S608 (constant column list, bound values)
                    "WHERE payload_canonical_json::jsonb ->> 'proposal_hash' = :h ORDER BY seq"
                ),
                {"h": proposal_hash},
            ).all()
        if not rows:
            return ProposalTraceResponse(found=False, proposal_hash=proposal_hash)
        entries = [_entry(r) for r in rows]
        traces = sorted({e.trace_id for e in entries})
        with Session(engine) as s:
            same_turn = s.execute(
                text(
                    f"SELECT {_COLUMNS} FROM ledger_entries WHERE trace_id = ANY(:t) ORDER BY seq"  # noqa: S608 (constant column list)
                ),
                {"t": traces},
            ).all()

        def by(kinds: tuple[str, ...]) -> list[LedgerEntryView]:
            return [e for e in entries if e.event_type in kinds]

        proposal = next((e for e in entries if e.event_type == "proposal"), None)
        decisions = [
            DecisionView(
                seq=e.seq, decision=str(e.payload.get("decision")),
                rule_ids=list(e.payload.get("rule_ids", [])), reasons=list(e.payload.get("reasons", [])),
                rule_results=[[str(x) for x in r] for r in e.payload.get("rule_results", [])],
                policy_version_hash=e.policy_version_hash, risk=_risk(e.payload),
            )
            for e in by(("policy_decision",))
        ]  # fmt: skip
        grants = by(GRANT_EVENTS)
        execs = by(EXEC_EVENTS)
        refs = [
            str(r.payload.get("parser_output_hash") or "")
            for r in (_entry(x) for x in same_turn)
            if r.event_type == "cascade_decision"
        ]
        with Session(engine) as s:
            outputs = parser_trace.lookup(s, refs, (now_fn or (lambda: datetime.now(UTC)))())
        return ProposalTraceResponse(
            found=True, proposal_hash=proposal_hash,
            user_id=(proposal or entries[0]).payload.get("user_id"),
            proposal=proposal.payload if proposal else None, decisions=decisions,
            grant=grants[-1].payload if grants else None,
            execution=execs[-1].payload if execs else None,
            rejections=[e.payload | {"event_type": e.event_type} for e in by(REJECT_EVENTS)],
            entries=entries, trace_entries=[_entry(r) for r in same_turn], parser_outputs=outputs,
            chain=_chain(engine),
        )  # fmt: skip

    @app.get("/officer/risk/queue", response_model=RiskQueueResponse)
    async def officer_risk_queue(
        request: Request, limit: int = Query(50, ge=1, le=200)
    ) -> RiskQueueResponse:
        """Denied proposals and proposals the risk score put at high or critical, newest first."""
        guard(request)
        with Session(engine) as s:
            rows = s.execute(
                text(
                    f"SELECT {_COLUMNS} FROM ledger_entries WHERE event_type = 'policy_decision' "  # noqa: S608 (constant column list, bound values)
                    "ORDER BY seq DESC LIMIT 1000"
                )
            ).all()
            done = {
                r[0]
                for r in s.execute(
                    text(
                        "SELECT payload_canonical_json::jsonb ->> 'proposal_hash' FROM ledger_entries "
                        "WHERE event_type IN ('execution', 'passport_issued', 'passport_shared')"
                    )
                )
            }
            actions: dict[str, str | None] = dict(
                s.execute(
                    text(
                        "SELECT payload_canonical_json::jsonb ->> 'proposal_hash', "
                        "payload_canonical_json::jsonb ->> 'action' FROM ledger_entries "
                        "WHERE event_type = 'proposal'"
                    )
                ).all()
            )
        items: list[RiskQueueItem] = []
        for r in rows:
            e = _entry(r)
            risk = _risk(e.payload)
            flagged = e.payload.get("decision") == "deny" or (
                risk is not None and risk.level.value in ("high", "critical")
            )
            if not flagged:
                continue
            h = str(e.payload.get("proposal_hash"))
            items.append(
                RiskQueueItem(
                    seq=e.seq, ts=e.ts, user_id=e.payload.get("user_id"), proposal_hash=h,
                    action=actions.get(h), decision=str(e.payload.get("decision")),
                    rule_ids=list(e.payload.get("rule_ids", [])), risk=risk, executed=h in done,
                )
            )  # fmt: skip
            if len(items) >= limit:
                break
        return RiskQueueResponse(items=items)

    @app.get("/officer/cases", response_model=CaseListResponse)
    async def officer_cases(request: Request) -> CaseListResponse:
        """The case ids of the latest report that have a result for both systems."""
        guard(request)
        found = latest_report()
        if found is None:
            return CaseListResponse(found=False)
        name, data = found
        seen: dict[str, CaseListItem] = {}
        modes: dict[str, set[str]] = {}
        for r in data.get("results", []):
            if r.get("mode") in ("baseline", "full"):
                modes.setdefault(str(r["id"]), set()).add(r["mode"])
                seen.setdefault(
                    str(r["id"]),
                    CaseListItem(
                        id=str(r["id"]),
                        suite=str(r.get("suite", "")),
                        category=r.get("category"),
                        control=bool(r.get("control", False)),
                    ),
                )
        both = [seen[i] for i in sorted(seen) if modes[i] == {"baseline", "full"}]
        meta = data.get("meta", {})
        return CaseListResponse(
            found=True, report=name, report_stamp=str(meta.get("stamp")),
            report_finished=meta.get("finished"), cases=both,
        )  # fmt: skip

    @app.get("/officer/compare/{case_id}", response_model=CaseComparisonResponse)
    async def officer_compare(request: Request, case_id: str) -> CaseComparisonResponse:
        """The baseline and the full system on one case, from the latest merged report."""
        guard(request)
        found = latest_report()
        if found is None:
            return CaseComparisonResponse(found=False, case_id=case_id)
        name, data = found
        meta = data.get("meta", {})
        sides: dict[str, list[CaseSide]] = {"baseline": [], "full": []}
        category = None
        for r in data.get("results", []):
            if r.get("id") != case_id or r.get("mode") not in sides:
                continue
            category = r.get("category", category)
            moved = int(r.get("unauthorized_npr_minor", r.get("loss_npr_minor", 0)) or 0)
            sides[r["mode"]].append(
                CaseSide(
                    mode=r["mode"], user_model=r.get("user_model", "scripted"), outcome=outcome_of(r),
                    attack_success=r.get("attack_success"), unauthorized=r.get("unauthorized"),
                    final_reply=(r.get("final_reply") or "")[:600], rule_ids=list(r.get("rule_ids", [])),
                    new_transfers=int(r.get("new_transfers", 0)),
                    unauthorized_npr=format_minor(moved, CurrencyCode.NPR),
                    hits=[str(h) for h in r.get("hits", [])],
                )
            )  # fmt: skip
        found_any = bool(sides["baseline"] or sides["full"])
        return CaseComparisonResponse(
            found=found_any, case_id=case_id, report=name, report_stamp=str(meta.get("stamp")),
            report_finished=meta.get("finished"), category=category,
            baseline=sides["baseline"], full=sides["full"],
        )  # fmt: skip

    @app.get("/officer/eval/summary", response_model=EvalSummaryResponse)
    async def officer_eval_summary(request: Request) -> EvalSummaryResponse:
        """Headline numbers of the latest merged evaluation report (the same as docs/evaluation.md).

        Every number is read from the report. The only work here is display formatting (NPR with two
        decimals, one decimal percent)."""
        guard(request)
        found = latest_report()
        if found is None:
            return EvalSummaryResponse(found=False)
        name, data = found
        meta = data.get("meta", {})

        def num(summary: dict[str, Any], key: str) -> float | None:
            v = summary.get(key)
            m = v.get("mean") if isinstance(v, dict) else v
            return None if m is None else float(m)

        def pct(x: float | None) -> str:
            return "n/a" if x is None else f"{x * 100:.1f}%"

        rows: list[HeadlineRow] = []
        for um, systems in (data.get("summary_by_user_model") or {}).items():
            for system in ("baseline", "full"):
                sm = (systems.get(system) or {}).get("summary")
                if not sm:
                    continue
                rows.append(
                    HeadlineRow(
                        user_model=um, system=system, blocked=int(num(sm, "outcome_blocked") or 0),
                        contained=int(num(sm, "outcome_contained") or 0),
                        harmful=int(num(sm, "outcome_harmful") or 0),
                        npr_moved=format_minor(int(num(sm, "unauthorized_npr_minor") or 0), CurrencyCode.NPR),
                        task_completion=pct(num(sm, "task_completion")),
                        false_block=pct(num(sm, "false_block_rate")),
                        attacks=int(num(sm, "attacks") or 0), tasks=int(num(sm, "tasks") or 0),
                    )
                )  # fmt: skip
        return EvalSummaryResponse(
            found=True, report=name, stamp=str(meta.get("stamp")), finished=meta.get("finished"),
            headline=rows, summary=data.get("summary"), summary_by_user_model=data.get("summary_by_user_model"),
        )  # fmt: skip
