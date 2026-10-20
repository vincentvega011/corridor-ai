"""The agent graph (docs/spec/architecture.md 4.2 and 5.15): a LangGraph StateGraph with a checkpointer.

Nodes: ingest, read_untrusted, parse, clarify, plan, run_read_tools, specialists, propose, policy,
request_confirmation, await_confirmation (a LangGraph ``interrupt``), execute, compose, ground, emit.

Safety shape (INV-1, INV-3, INV-4, INV-10):
- The LLMs only produce structured drafts; this module's code decides everything that matters.
  Payee resolution, source account choice, amount provenance, idempotency key, route and condition
  evaluation are deterministic code. The planner never names a payee or an amount that is used.
- The user's attachments are never passed to any model (the reader arrives in P2).
- The only write is ``corridor.executor`` after a confirmation grant bound to the proposal hash,
  after a fresh policy decision, and with a ledger entry in the same transaction.
- The deterministic policy engine decides what each proposal needs (confirm, MFA or deny); every
  proposal, decision, confirmation and rejection is appended to the ledger before the reply.
- Any exception in a node ends the turn with a safe message and an ``error`` event.

State holds plain JSON values only (it is checkpointed), so provenance survives as strings.
TODO-P2-2: grounding verification and the quarantined reader.
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, TypedDict

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from corridor.ablation import NONE, Ablation, Ablations
from corridor.ablation_hooks import evaluate_ablated
from corridor.agent import compose as composer
from corridor.agent.conditions import evaluate_condition
from corridor.agent.events import Emitter, EventSink
from corridor.agent.payees import resolve_payee
from corridor.agent.planning import (
    ACTION_FOR_INTENT,
    PROMPT_VERSION,
    PlanInvalid,
    ValidatedPlan,
    build_planner_messages,
    default_read_plan,
    validate_plan,
)
from corridor.agent.proposals import (
    MoneyPlan,
    ProposalError,
    build_add_payee,
    build_add_typed_payee,
    build_contribution_request,
    build_invoice_payment,
    build_passport_issue,
    build_passport_share,
    build_transfer,
    compute_route,
    decide_money,
    proposal_card,
)
from corridor.agent.reasons import render_reasons
from corridor.agent.settings import CorridorConfig
from corridor.agent.toolrun import ToolError, run_read_tool
from corridor.confirm import record_event, record_policy_denial
from corridor.contracts.action import ActionProposal
from corridor.contracts.events import AgentEventType as ET
from corridor.contracts.intent import IntentType, LanguageTag
from corridor.contracts.ledger import LedgerActor, LedgerEntry, LedgerEventType
from corridor.contracts.llm_outputs import PlannerOutput
from corridor.contracts.money import CurrencyCode
from corridor.contracts.policy import Decision
from corridor.contracts.provenance import Provenance
from corridor.contracts.routing import RouteRecommendation
from corridor.contracts.tools import ToolResult
from corridor.executor import (
    ContributionRequested,
    ExecutionRejected,
    Executor,
    PassportDone,
    PayeeAdded,
    required_level,
)
from corridor.formatting import format_local_time, format_minor
from corridor.grounding import ToolResultRegistry, ground_reply
from corridor.grounding.verify import SAFE_EXECUTED
from corridor.ledger import Ledger
from corridor.llm import Gateway, LLMError, ModelRole
from corridor.nlu.clarify import question_for, style_for
from corridor.nlu.deadline_reply import classify as deadline_reply_kind
from corridor.nlu.lang_pack import LangPacks
from corridor.nlu.parser import MODEL_FAILURES, parse
from corridor.nlu.rules import classify_intent, is_attachment_imperative
from corridor.nlu.typed_payee import find_account, find_name
from corridor.passport.claims import STATEMENTS as CLAIM_STATEMENTS
from corridor.passport.claims import preview_claim_ids
from corridor.passport.select import audience_from_text, claims_from_text
from corridor.policy import PolicyConfig, load_policy_config
from corridor.reader import ReadResult, load_document, read_pasted, split_message
from corridor.reader.model import redact_numbers
from corridor.risk import assess as assess_risk
from corridor.risk import risk_view
from corridor.sandbox import parser_trace, vault
from corridor.sandbox.account_state import build_account_state
from corridor.sandbox.doc_payee import document_names, payee_terms
from corridor.sandbox.models import Payee

log = structlog.get_logger("corridor.agent")

ACTION_INTENTS = frozenset({IntentType.SEND_MONEY, IntentType.PAY_INVOICE})
PASSPORT_INTENTS = frozenset({IntentType.CREDIT_PASSPORT_ISSUE, IntentType.CREDIT_PASSPORT_SHARE})
READ_INTENTS = frozenset(
    {
        IntentType.CHECK_BALANCE,
        IntentType.SPENDING_SUMMARY,
        IntentType.COMPARE_ROUTES,
        IntentType.CONVERT_CURRENCY,
    }
)
INFO_INTENTS = frozenset({IntentType.HELP, IntentType.EXPLAIN_DECISION, IntentType.OTHER})


class AgentState(TypedDict, total=False):
    """Checkpointed state. Plain JSON values only."""

    thread_id: str
    user_id: str
    trace_id: str
    structured: dict[str, Any] | None
    next_seq: int
    text: str
    attachments: list[dict[str, Any]]
    today: str
    profile: dict[str, Any]
    pending: dict[str, Any] | None
    frame: dict[str, Any] | None
    status: str
    reply: str
    reply_source: str
    reads: dict[str, dict[str, Any]]
    payee: dict[str, Any] | None
    invoice_id: str | None
    plan: dict[str, Any] | None
    money: dict[str, Any] | None
    route: dict[str, Any] | None
    route_provider: str | None
    proposal: dict[str, Any] | None
    proposal_card: dict[str, Any] | None
    proposal_hash: str | None
    confirmed_hash: str | None
    grant_id: str | None
    attachments_in: list[dict[str, Any]]
    forwarded: str | None
    pasted_parts: list[str]
    docs: list[str]
    doc_summaries: list[str]
    doc_fields: dict[str, dict[str, Any]]
    uncertain_paste: bool
    model_text: str
    exec_facts: dict[str, Any] | None
    grounding: dict[str, Any] | None
    decision: dict[str, Any] | None
    required_level: str | None
    add_payee_doc: str | None
    typed_accounts: list[dict[str, Any]]
    reply_extra: dict[str, Any] | None
    add_payee_typed: dict[str, Any] | None
    plan_steps: list[dict[str, Any]] | None
    passport_action: dict[str, Any] | None
    risk: dict[str, Any] | None
    plan_index: int


@dataclass
class Deps:
    """Everything the graph needs from outside. Nothing here is user or model controlled."""

    engine: Engine
    gateway: Gateway
    packs: LangPacks
    corridors: CorridorConfig
    planner_provider: str | None = (
        None  # eval can force a provider; None keeps the configured failover
    )
    planner_model: str | None = None
    policy: PolicyConfig = field(default_factory=load_policy_config)
    ledger: Ledger = field(default_factory=Ledger)
    secrets_dir: Path | None = None
    ablations: Ablations = NONE  # evaluation only (ADR 0010); validated on construction
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC)


def _sink(config: RunnableConfig) -> EventSink | None:
    sink = config.get("configurable", {}).get("sink")
    return sink if isinstance(sink, EventSink) else None


def _em(state: AgentState, config: RunnableConfig) -> Emitter:
    return Emitter(
        _sink(config),
        state.get("trace_id", ""),
        state.get("thread_id", ""),
        state.get("next_seq", 0),
    )


def _tr(dump: dict[str, Any]) -> ToolResult:
    return ToolResult.model_validate(dump)


def _tz(state: AgentState, cfg: CorridorConfig) -> str | None:
    """The user's time zone from their region of residence (config), or None (shown in UTC)."""
    region = ((state.get("profile") or {}).get("home_region")) or ""
    return cfg.region_timezone.get(region)


def _today(state: AgentState) -> date:
    return date.fromisoformat(state["today"])


def _lang(state: AgentState) -> tuple[LanguageTag, bool]:
    f = state.get("frame") or {}
    return LanguageTag(f.get("language", "en")), bool(f.get("deva", False))


def _slot(frame: dict[str, Any], name: str) -> dict[str, Any] | None:
    found = frame.get("slots", {}).get(name)
    return found if isinstance(found, dict) else None


def _display_slots(frame: dict[str, Any]) -> dict[str, Any]:
    ccy = (_slot(frame, "currency") or {}).get("value")
    out: dict[str, Any] = {}
    for name, s in frame.get("slots", {}).items():
        v = s["value"]
        if name == "amount" and isinstance(v, int):
            try:
                v = format_minor(v, CurrencyCode(ccy)) if ccy else f"{Decimal(v) / 100:,.2f}"
            except ValueError:
                v = f"{Decimal(v) / 100:,.2f}"
        elif name == "payee_ref" and isinstance(v, dict):
            v = v.get("text")
        out[name] = {"value": v, "provenance": s["provenance"]}
    return out


def build_graph(deps: Deps, checkpointer: Any) -> Any:
    """Build and compile the graph with ``deps`` and a checkpointer."""
    eng, gw, packs, cfgc = deps.engine, deps.gateway, deps.packs, deps.corridors
    pcfg, ledger = deps.policy, deps.ledger
    executor = Executor(eng, pcfg, ledger, deps.ablations, deps.secrets_dir)
    abl = deps.ablations

    def log_ledger(
        state: AgentState, em: Emitter, event: LedgerEventType, data: dict[str, Any],
        actor: LedgerActor = LedgerActor.AGENT,
    ) -> LedgerEntry:  # fmt: skip
        """Append one ledger entry (INV-8). Raises LedgerError, which fails the turn closed."""
        with Session(eng) as s, s.begin():
            entry = ledger.append(
                s, trace_id=state["trace_id"], actor=actor, event_type=event,
                user_id=state["user_id"], session_id=state["thread_id"], data=data,
                policy_version_hash=pcfg.version_hash, now=deps.now_fn(),
            )  # fmt: skip
        note_ledger(em, entry)
        return entry

    def count_failed_attempt(state: AgentState, em: Emitter) -> None:
        """A confirmation for the wrong hash is a failed high risk attempt (INV-11)."""
        now = deps.now_fn()
        with Session(eng) as s, s.begin():
            lock_reason = record_event(s, state["user_id"], "failed_high_risk", now, pcfg)
            if lock_reason is not None:
                entry = ledger.append(
                    s, trace_id=state["trace_id"], actor=LedgerActor.SYSTEM,
                    event_type=LedgerEventType.SAFETY_LOCK, user_id=state["user_id"],
                    session_id=state["thread_id"], data={"action": "locked", "reason": lock_reason},
                    policy_version_hash=pcfg.version_hash, now=now,
                )  # fmt: skip
                note_ledger(em, entry)

    def note_ledger(em: Emitter, entry: LedgerEntry) -> None:
        em.emit(
            ET.LEDGER_APPEND,
            {
                "seq": entry.seq,
                "event_type": entry.event_type.value,
                "entry_hash": entry.entry_hash,
            },
        )

    def run_tool(
        state: AgentState, em: Emitter, step_id: str, tool: str, args: dict[str, Any]
    ) -> ToolResult:
        em.emit(ET.STEP_START, {"step_id": step_id, "tool": tool, "args": args})
        res = run_read_tool(eng, state["user_id"], tool, args, _today(state), now=deps.now_fn())
        em.emit(
            ET.STEP_RESULT,
            {"step_id": step_id, "tool": tool, "data": res.data, "fact_count": len(res.facts)},
        )
        return res

    # ------------------------------------------------------------------ ingest
    async def ingest(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        with eng.connect() as conn:
            row = conn.execute(
                text("SELECT home_region, locale FROM users WHERE id = :u"), {"u": state["user_id"]}
            ).first()
        if row is None:
            return {"status": "rejected", "reply": "unknown user", "reply_source": "system"}
        raw_text = state.get("text", "")
        split = split_message(raw_text, state.get("forwarded"))
        # Pasted third party content never reaches the parser or any model except the quarantined
        # reader (INV-3). A certain split removes it from the text; an uncertain split keeps the text
        # for the parser but forces payee, amount and currency to be asked again (INV-4).
        single_line = "single_line_mixed" in split.reasons
        # Only the user's own lines are ever parsed. A message that cannot be separated at all is not
        # shown to any model: the user is asked to say it again in their own words.
        user_text = raw_text if not split.has_pasted else ("" if single_line else split.user_text)
        pasted = list(split.pasted)
        if abl.has(Ablation.NO_QUARANTINE):  # evaluation only: third party text goes in as text
            user_text = "\n".join(p for p in (raw_text, state.get("forwarded") or "") if p)
            single_line = False
            split_certain = True
        else:
            split_certain = split.certain
        # An account number in the user's own words is the only way a typed payee gets USER_TYPED
        # provenance (ADR 0018). Only the keyed hash and last four digits are kept in the state.
        found_acct = find_account(
            user_text, "\n".join(pasted),
            uncertain_split=split.has_pasted and not split_certain,
            hash_fn=lambda d: vault.account_hash(d, deps.secrets_dir),
        )  # fmt: skip
        typed_accounts = (
            [{"hash": found_acct.account_hash, "last4": found_acct.last4, "provenance": found_acct.provenance.value}]
            if found_acct is not None else []
        )  # fmt: skip
        return {
            "typed_accounts": typed_accounts, "add_payee_typed": None,
            "status": "running", "frame": None, "payee": None, "invoice_id": None, "add_payee_doc": None,
            "plan_steps": None, "plan_index": 0, "passport_action": None,
            "plan": None,
            "money": None, "route": None, "route_provider": None, "proposal": None, "proposal_card": None,
            "proposal_hash": None, "confirmed_hash": None, "reply": "", "reply_source": "", "reads": {},
            "profile": {"home_region": row[0], "locale": row[1]},
            "text": user_text, "pasted_parts": pasted, "docs": [], "doc_summaries": [],
            "doc_fields": {}, "uncertain_paste": split.has_pasted and not split_certain,
            "model_text": user_text,
            "exec_facts": None, "grounding": None, "forwarded": None,
        }  # fmt: skip

    def after_ingest(state: AgentState) -> str:
        if state.get("status") == "rejected":
            return "emit"
        has_docs = state.get("attachments") or state.get("pasted_parts")
        return "read_untrusted" if has_docs else "parse"

    # ------------------------------------------------------------------ read_untrusted (quarantined reader)
    async def read_untrusted(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        """Load uploaded documents and read pasted third party text, all as UNTRUSTED data.

        Only typed summaries leave this node (INV-3): the raw pasted text is dropped from the state
        as soon as the reader has processed it."""
        em = _em(state, config)
        lang, deva = _lang(state)
        now = deps.now_fn()
        results: list[ReadResult] = []
        for att in state.get("attachments", [])[:5]:
            doc = load_document(eng, str(att.get("doc_id", "")), state["user_id"])
            if doc is None:
                em.emit(ET.REJECTED, {"reason": "document_not_found"})
                return {
                    "status": "rejected", "reply": composer.template("document_not_found", lang, deva, packs),
                    "reply_source": "template", "pasted_parts": [], "next_seq": em.seq,
                }  # fmt: skip
            results.append(doc)
        try:
            for part in state.get("pasted_parts", [])[:3]:
                results.append(
                    await read_pasted(gw, eng, state["user_id"], part, now, deps.secrets_dir)
                )
        except Exception as exc:  # the reader failed: nothing from the pasted text is used (INV-10)
            log.error("reader_failed", error=type(exc).__name__)
            em.emit(ET.ERROR, {"reason": "reader_unavailable"})
            return {
                "status": "rejected", "reply": composer.template("reader_failed", lang, deva, packs),
                "reply_source": "template", "pasted_parts": [], "next_seq": em.seq,
            }  # fmt: skip
        for r in results:
            em.emit(
                ET.TAINT_FLAG,
                {
                    "field": "document", "doc": r.doc_hash[:8], "kind": r.kind,
                    "provenance": "untrusted", "injection_suspected": r.injection_suspected,
                    "signals": list(r.signals), "summary": r.summary,
                },
            )  # fmt: skip
            log_ledger(
                state, em, LedgerEventType.READER_EXTRACTION,
                {"doc_hash": r.doc_hash, "kind": r.kind, "injection_suspected": r.injection_suspected,
                 "signals": list(r.signals)},
            )  # fmt: skip
        update: dict[str, Any] = {
            "docs": [r.doc_hash for r in results], "doc_summaries": [r.summary for r in results],
            "doc_fields": {r.doc_hash: r.fields for r in results}, "pasted_parts": [],
            "next_seq": em.seq,
        }  # fmt: skip
        if not (state.get("text") or "").strip() and state.get("uncertain_paste"):
            try:  # nothing of the user's own voice could be told apart: ask for the recipient
                saved = run_tool(state, em, "pre1", "get_payees", {}).data.get("payees", [])
            except ToolError:
                saved = []
            names = tuple(f"{p['display_name']} {p['account_no']}" for p in saved)
            style = style_for(lang, deva)
            q = packs.clarify["paste_confirm"][style].format(options=" / ".join(names) or "-")
            em.emit(ET.CLARIFY, {"slot": "payee_ref", "question": q, "options": list(names)})
            return {
                **update,
                "status": "clarify",
                "reply": q,
                "reply_source": "template",
                "pending": None,
            }
        if not (
            state.get("text") or ""
        ).strip():  # only documents were sent: describe them, ask what to do
            warn = any(r.injection_suspected for r in results)
            line = " ".join(r.summary for r in results)
            reply = composer.template("document_received", lang, deva, packs, line=line)
            if warn:
                reply += " " + composer.template("document_warning", lang, deva, packs)
            return {**update, "status": "done", "reply": reply, "reply_source": "template"}
        return update

    def after_read_untrusted(state: AgentState) -> str:
        return "emit" if state.get("status") in ("rejected", "done", "clarify") else "parse"

    # ------------------------------------------------------------------ parse
    def _structured_frame(state: AgentState, em: Emitter) -> dict[str, Any]:
        """The frame of a button flow (ADR 0021): built by code from what the button carries. No parser, no model.
        Amounts and the payee come from the customer's own form, so they carry USER_TYPED provenance, the same as
        words they typed (INV-4); the payee itself is looked up by id among the customer's stored payees."""
        act = state["structured"] or {}
        slots: dict[str, Any] = {}
        if act.get("kind") == "send_money":
            slots["amount"] = {
                "value": int(act["amount_minor"]),
                "provenance": "user_typed",
                "source_ref": "button",
            }
            slots["currency"] = {
                "value": str(act["currency"]),
                "provenance": "user_typed",
                "source_ref": "button",
            }
            intent = IntentType.SEND_MONEY
        else:
            intent = IntentType.PAY_INVOICE
        language = LanguageTag.NE if act.get("language") == "ne" else LanguageTag.EN
        out_frame = {
            "intent": intent.value, "language": language.value, "slots": slots, "missing": [], "ambiguous": {},
            "confidence_bps": 10000, "parser": "structured", "warnings": [], "unsupported_currency": None,
            "blocked": False, "deva": language is LanguageTag.NE,
            "currencies_in_order": [str(act["currency"])] if act.get("currency") else [],
            "continued": False, "failed": False, "pending_awaiting": None,
        }  # fmt: skip
        em.emit(
            ET.INTENT,
            {
                "intent": intent.value, "language": language.value, "confidence_bps": 10000, "rule": "structured",
                "parser": "structured", "slots": _display_slots(out_frame),
                "cascade": {"used": "structured", "cascaded": False, "reasons": ["button"]},
                "continued_previous_request": False,
            },
        )  # fmt: skip
        log_ledger(
            state, em, LedgerEventType.CASCADE_DECISION,
            {
                "intent": intent.value, "language": language.value, "parser": "structured", "cascaded": False,
                "reasons": ["button"], "confidence_bps": 10000,
                "slots": {name: v["provenance"] for name, v in slots.items()},
                "parser_output_hash": "",
            },
        )  # fmt: skip
        return out_frame

    async def parse_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        em = _em(state, config)
        if state.get("structured"):
            out = _structured_frame(state, em)
            return {"frame": out, "docs": [], "next_seq": em.seq}
        res = await parse(state["text"], _today(state), gateway=gw, packs=packs)
        frame = res.frame
        pending = state.get("pending")
        slots = {
            k.value: {
                "value": v.value,
                "provenance": v.provenance.value,
                "source_ref": v.source_ref,
            }
            for k, v in frame.slots.items()
        }
        if state.get("uncertain_paste"):
            # the split of the user's words from pasted content is uncertain: nothing about the
            # recipient or the amount may count as typed by the user (INV-4)
            for name in list(slots):
                slots[name] = {**slots[name], "provenance": "untrusted"}
        intent = frame.intent
        language = frame.language
        ambiguous = {k: list(v) for k, v in res.ambiguous.items()}
        keep_prev = False
        carried_docs: list[str] = []
        if pending and pending.get("intent"):
            guess = classify_intent(state["text"], res.normalised, packs, _today(state)).intent
            prev_intent = IntentType(pending["intent"])
            keep_prev = prev_intent in ACTION_INTENTS | READ_INTENTS and (
                intent in (IntentType.OTHER, IntentType.HELP) or guess is None
            )
            if keep_prev:
                carried_docs = list(pending.get("docs", []))
                merged = dict(pending.get("slots", {}))
                merged.update(slots)
                slots, intent, language = (
                    merged,
                    prev_intent,
                    LanguageTag(pending.get("language", language.value)),
                )
                if pending.get("awaiting") == "deadline_past" and "deadline" not in dict(
                    (k, 1) for k in {k.value for k in frame.slots}
                ):
                    slots.pop("deadline", None)  # the user said to continue without that date
        deadline_reply: str | None = None
        if pending and pending.get("awaiting") == "deadline_past" and "deadline" not in slots:
            # the answer to "that date has passed": cancel, go ahead without a date, or unrelated (ask again)
            deadline_reply = deadline_reply_kind(state["text"]) or "unrelated"
            if not keep_prev and deadline_reply in ("cancel", "affirm"):
                keep_prev = True
                merged = dict(pending.get("slots", {}))
                slots, intent = merged, IntentType(pending["intent"])
                language = LanguageTag(pending.get("language", language.value))
                carried_docs = list(pending.get("docs", []))
            if deadline_reply in ("cancel", "affirm") and keep_prev:
                slots = dict(pending.get("slots", {}))  # the words of the answer name nobody
            if deadline_reply == "affirm" and keep_prev:
                slots.pop("deadline", None)  # go ahead without that date
            if deadline_reply == "unrelated" and keep_prev:
                slots = dict(pending.get("slots", {}))  # keep the old deadline so it is asked again
        rule_override = False
        if (
            state.get("docs")
            and not keep_prev
            and intent
            in (IntentType.HELP, IntentType.OTHER, IntentType.SEND_MONEY, IntentType.PAY_INVOICE)
            and not ({"amount", "payee_ref"} & set(slots))
            and is_attachment_imperative(state["text"], packs)
        ):  # "pay this" + an attached document: pay that document's invoice (own words, by rule)
            rule_override = intent is not IntentType.PAY_INVOICE or frame.confidence_bps < 8000
            intent, frame_conf = IntentType.PAY_INVOICE, max(frame.confidence_bps, 9000)
        else:
            frame_conf = frame.confidence_bps
        need = packs.required_slots.get(intent.value, ())
        missing = [s for s in need if s not in slots and s not in ambiguous]
        out_frame = {
            "intent": intent.value, "language": language.value, "slots": slots, "missing": missing,
            "ambiguous": ambiguous, "confidence_bps": frame_conf, "parser": frame.parser,
            "warnings": list(res.warnings), "unsupported_currency": res.unsupported_currency,
            "blocked": res.blocked, "deva": any("ऀ" <= c <= "ॿ" for c in state["text"]),
            "currencies_in_order": [c.code.value for c in res.normalised.currencies if c.code],
            "models_unavailable": res.cascade.used == "none" and any(n in r for r in res.cascade.reasons for n in MODEL_FAILURES),
            "cloud_unavailable": res.cascade.used != "none" and res.cascade.cascaded and any(r.startswith("cloud_error:") and any(n in r for n in MODEL_FAILURES) for r in res.cascade.reasons),
            "continued": keep_prev, "failed": res.cascade.used == "none" and not res.blocked and not rule_override,
            "pending_awaiting": (pending or {}).get("awaiting"), "deadline_reply": deadline_reply,
        }  # fmt: skip
        em.emit(
            ET.INTENT,
            {
                "intent": intent.value, "language": language.value, "confidence_bps": frame_conf,
                "rule": "attachment_imperative" if rule_override else None,
                "parser": frame.parser, "slots": _display_slots(out_frame),
                "cascade": {"used": res.cascade.used, "cascaded": res.cascade.cascaded, "reasons": list(res.cascade.reasons)},
                "continued_previous_request": keep_prev,
            },
        )  # fmt: skip
        with Session(eng) as s, s.begin():
            parser_ref = parser_trace.store(
                s, trace_id=state["trace_id"], user_id=state["user_id"],
                masked_output=res.raw_output, now=deps.now_fn(),
            )  # fmt: skip
        log_ledger(
            state, em, LedgerEventType.CASCADE_DECISION,
            {
                "intent": frame.intent.value, "language": frame.language.value,
                "parser": res.cascade.used, "cascaded": res.cascade.cascaded,
                "reasons": list(res.cascade.reasons),
                "confidence_bps": res.cascade.final_confidence_bps,
                "slots": {name: v["provenance"] for name, v in slots.items()},  # provenance by slot name
                "parser_output_hash": parser_ref,  # only a reference; the masked text lives in the trace store and expires
            },
        )  # fmt: skip
        docs_now = list(dict.fromkeys([*carried_docs, *state.get("docs", [])]))
        return {"frame": out_frame, "docs": docs_now, "next_seq": em.seq}

    def _new_payee_document(
        state: AgentState, intent: IntentType, ref: Any, em: Emitter
    ) -> str | None:
        """The one attached document whose account is not a stored payee, when the user did not name a
        stored payee. None means the ordinary path (which may ask a question or reject)."""
        if intent not in ACTION_INTENTS:
            return None
        found = [
            h
            for h, f in state.get("doc_fields", {}).items()
            if (t := payee_terms(f)) is not None
            and not _stored_account(state["user_id"], t.account_hash)
        ]
        if len(found) != 1:
            return None
        value = (ref or {}).get("value") if isinstance(ref, dict) else None
        if isinstance(value, dict):
            try:
                saved = run_tool(state, em, "pre1", "get_payees", {}).data.get("payees", [])
            except ToolError:
                return None
            saved = _with_match_names(state["user_id"], saved)
            if resolve_payee(value, saved, packs).status != "none":
                return None  # the user named someone they already pay
        return found[0]

    def _with_match_names(user_id: str, payees: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Add the sanitised document name as ``match_name`` for deterministic matching only. Models never
        see it: ``get_payees`` returns a fixed label for these payees (INV-3)."""
        with Session(eng) as s:
            names = document_names(s, user_id)
        return [
            {**p, "match_name": names[p["payee_id"]]} if p["payee_id"] in names else p
            for p in payees
        ]

    def _stored_account(user_id: str, account_hash: str) -> bool:
        with Session(eng) as s:
            return (
                s.scalars(
                    select(Payee.id).where(
                        Payee.user_id == user_id, Payee.account_no_hash == account_hash
                    )
                ).first()
                is not None
            )

    # ------------------------------------------------------------------ clarify (also: payee resolution)
    async def clarify(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        em = _em(state, config)
        frame = state["frame"] or {}
        lang, deva = _lang(state)
        style = style_for(lang, deva)
        intent = IntentType(frame["intent"])
        slots = frame["slots"]

        def done(
            reply: str, status: str, pending: dict[str, Any] | None = None, **extra: Any
        ) -> dict[str, Any]:
            return {
                "status": status,
                "reply": reply,
                "reply_source": "template",
                "pending": pending,
                "next_seq": em.seq,
                **extra,
            }

        def ask(
            key: str,
            awaiting: str,
            options: tuple[str, ...] = (),
            fmt: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            if fmt:
                q = packs.clarify[key][style].format(**fmt)
            else:
                q = question_for(awaiting if key == awaiting else key, style, packs, options)
            em.emit(ET.CLARIFY, {"slot": awaiting, "question": q, "options": list(options)})
            pend = {
                "intent": intent.value,
                "slots": slots,
                "language": lang.value,
                "awaiting": awaiting,
                "docs": list(state.get("docs", [])),
            }
            return done(q, "clarify", pend)

        def reject(reason: str, key: str, **fmt: Any) -> dict[str, Any]:
            reply = composer.template(key, lang, deva, packs, **fmt)
            em.emit(ET.REJECTED, {"reason": reason})
            return done(reply, "rejected", None)

        if frame.get("blocked"):
            return reject("sensitive_content", "sensitive_content")
        if frame.get("deadline_reply") == "cancel":
            em.emit(ET.REJECTED, {"reason": "user_cancelled"})
            return done(composer.template("cancelled", lang, deva, packs), "rejected", None)
        if frame.get("failed") and frame.get("models_unavailable"):
            return reject(
                "assistant_unavailable", "assistant_unavailable"
            )  # no model answered: say so, never "rephrase"
        if frame.get("cloud_unavailable") and frame["confidence_bps"] < 8000:
            # the local parser was unsure and the stronger model that would have decided could not answer (offline
            # replay, no key, quota): asking the user to rephrase would be misleading, so say so once
            return reject("assistant_unavailable", "assistant_unavailable")
        if frame.get("failed") or (intent is IntentType.OTHER and frame["confidence_bps"] == 0):
            q = packs.clarify["generic"][style]
            em.emit(ET.CLARIFY, {"slot": "intent", "question": q, "options": []})
            return done(q, "clarify", state.get("pending"))
        if intent in INFO_INTENTS:
            return done(composer.template("help", lang, deva, packs), "done", None)
        if frame.get("unsupported_currency"):
            return reject("unsupported_currency", "unsupported_currency")
        if intent is IntentType.ADD_PAYEE:
            return _typed_payee_prepare(state, em, frame, slots, lang, deva, ask, done)
        if intent not in ACTION_INTENTS | READ_INTENTS | PASSPORT_INTENTS:
            return reject("unsupported_intent", "unsupported_intent")
        if frame["confidence_bps"] < 8000:
            q = packs.clarify["generic"][style]
            em.emit(ET.CLARIFY, {"slot": "intent", "question": q, "options": []})
            return done(q, "clarify", None)

        if intent in PASSPORT_INTENTS:
            return _passport_prepare(state, em, intent, lang, deva, style, ask, done)
        amb = frame["ambiguous"]
        reads = dict(state.get("reads", {}))
        # ---- read only intents: only slot questions
        if intent in READ_INTENTS:
            for slot in ("amount", "currency"):
                if slot in amb:
                    return ask(slot, slot, tuple(amb[slot]))
            if (
                intent in (IntentType.COMPARE_ROUTES, IntentType.CONVERT_CURRENCY)
                and "amount" in frame["missing"]
            ):
                return ask("amount", "amount")
            if (
                intent is IntentType.CONVERT_CURRENCY
                and len(set(frame.get("currencies_in_order", []))) < 2
            ):
                return ask("currency", "currency")
            return {"status": "running", "next_seq": em.seq}
        # ---- action intents
        doc_for_payee = _new_payee_document(state, intent, slots.get("payee_ref"), em)
        if doc_for_payee is not None:
            # an attached document asks to pay an account that is not a stored payee: propose saving it as an
            # untrusted payee that is held for 24 hours (never a payment, never a model decision)
            try:
                reads["get_balances"] = run_tool(state, em, "pre2", "get_balances", {}).model_dump(
                    mode="json"
                )
            except ToolError:
                em.emit(ET.ERROR, {"reason": "tool_failed"})
                return done(
                    composer.template("planner_failed", lang, deva, packs), "rejected", None
                )
            return {
                "status": "running", "reads": reads, "payee": None, "invoice_id": None,
                "add_payee_doc": doc_for_payee, "next_seq": em.seq,
            }  # fmt: skip
        if intent is IntentType.SEND_MONEY:
            for slot in ("payee_ref", "amount", "currency", "deadline"):
                if slot in amb:
                    return ask(slot, slot, tuple(amb[slot]))
                if slot in frame["missing"]:
                    return ask(slot, slot)
            if state.get("uncertain_paste") and any(
                (slots.get(n) or {}).get("provenance") == "untrusted"
                for n in ("payee_ref", "amount")
            ):
                try:
                    saved = run_tool(state, em, "pre1", "get_payees", {}).data.get("payees", [])
                except ToolError:
                    saved = []
                names = tuple(f"{p['display_name']} {p['account_no']}" for p in saved)
                return ask(
                    "paste_confirm", "payee_ref", names, fmt={"options": " / ".join(names) or "-"}
                )
            for slot in ("payee_ref", "amount", "currency"):
                found = slots.get(slot)
                if found is not None and found["provenance"] != "user_typed":
                    return ask(
                        slot, slot
                    )  # INV-4: a value the model supplied without evidence is asked, never used
        else:
            for slot in ("currency", "deadline"):
                if slot in amb:
                    return ask(slot, slot, tuple(amb[slot]))
        # pre-reads: the user's own payees, accounts and (for invoices) open invoices
        try:
            reads["get_payees"] = run_tool(state, em, "pre1", "get_payees", {}).model_dump(
                mode="json"
            )
            reads["get_balances"] = run_tool(state, em, "pre2", "get_balances", {}).model_dump(
                mode="json"
            )
            if intent is IntentType.PAY_INVOICE:
                reads["list_invoices"] = run_tool(
                    state, em, "pre3", "list_invoices", {}
                ).model_dump(mode="json")
        except ToolError:
            em.emit(ET.ERROR, {"reason": "tool_failed"})
            return done(
                composer.template("planner_failed", lang, deva, packs),
                "rejected",
                None,
                reads=reads,
            )
        payees = _with_match_names(
            state["user_id"], _tr(reads["get_payees"]).data.get("payees", [])
        )
        payee: dict[str, Any] | None = None
        invoice_id: str | None = None
        ref = (slots.get("payee_ref") or {}).get("value")
        act = state.get("structured") or {}
        if intent is IntentType.SEND_MONEY and act.get(
            "payee_id"
        ):  # a button: the payee is chosen by id
            payee = next((p for p in payees if p["payee_id"] == act["payee_id"]), None)
            if payee is None:
                return ask("payee_unknown", "payee_ref", fmt={"name": "?"})
        if intent is IntentType.SEND_MONEY and isinstance(ref, dict):
            resolution = resolve_payee(ref, payees, packs)
            if resolution.status == "none":
                return ask("payee_unknown", "payee_ref", fmt={"name": str(ref.get("text", "?"))})
            if resolution.status == "many":
                names = tuple(f"{p['display_name']} {p['account_no']}" for p in resolution.matches)
                return ask("payee_many", "payee_ref", names, fmt={"options": " / ".join(names)})
            payee = resolution.payee
        if intent is IntentType.PAY_INVOICE:
            invoices = _tr(reads["list_invoices"]).data.get("invoices", [])
            if isinstance(ref, dict) and invoices:
                res2 = resolve_payee(ref, payees, packs)
                if res2.status == "one" and res2.payee:
                    invoices = [
                        i for i in invoices if i["beneficiary_payee_id"] == res2.payee["payee_id"]
                    ]
            doc_match = next(
                (
                    f.get("matched_invoice_id")
                    for f in state.get("doc_fields", {}).values()
                    if f.get("kind") == "invoice" and f.get("matched_invoice_id")
                ),
                None,
            )
            if doc_match:  # the attached document was matched to a stored invoice by code
                invoices = [i for i in invoices if i["invoice_id"] == doc_match]
            if act.get("invoice_id"):  # a button: the invoice is chosen by id, never guessed
                invoices = [i for i in invoices if i["invoice_id"] == act["invoice_id"]]
            if not invoices:
                return reject("no_open_invoice", "no_open_invoice")
            if len(invoices) > 1:
                names = tuple(
                    f"{i['invoice_id']} ({i['amount']}, due {i['due_date']})" for i in invoices
                )
                return ask("invoice_which", "invoice", names, fmt={"options": " / ".join(names)})
            invoice_id = invoices[0]["invoice_id"]
        # a deadline that is today or already past is confirmed with the user, never assumed
        dl = slots.get("deadline")
        if dl and (
            frame.get("pending_awaiting") != "deadline_past"
            or frame.get("deadline_reply") == "unrelated"
        ):
            d = dl["value"].get("date") if isinstance(dl["value"], dict) else None
            if d is None:
                return ask("deadline", "deadline")
            if date.fromisoformat(d) <= _today(state):
                return ask("deadline_past", "deadline_past", fmt={"date": d})
        return {
            "status": "running",
            "reads": reads,
            "payee": payee,
            "invoice_id": invoice_id,
            "next_seq": em.seq,
        }

    def after_clarify(state: AgentState) -> str:
        if state.get("status") != "running":
            return "emit"
        return (
            "propose"
            if state.get("add_payee_doc")
            or state.get("add_payee_typed")
            or state.get("passport_action")
            else "plan"
        )

    # ------------------------------------------------------------------ plan
    async def plan(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        em = _em(state, config)
        frame = state["frame"] or {}
        lang, deva = _lang(state)
        intent = IntentType(frame["intent"])

        def fail(unavailable: bool = False) -> dict[str, Any]:
            key = "assistant_unavailable" if unavailable else "planner_failed"
            return {
                "status": "rejected", "reply": composer.template(key, lang, deva, packs),
                "reply_source": "template", "next_seq": em.seq,
            }  # fmt: skip

        try:
            if state.get("structured"):
                # a button flow (ADR 0021): the reads are already done by code, so there is nothing for a model to plan
                vplan = default_read_plan(intent, [])
                planner_info = {
                    "model": None,
                    "provider": None,
                    "prompt": "deterministic",
                    "failover_from": None,
                }
            elif intent in ACTION_INTENTS:
                reads = state.get("reads", {})
                accounts = [
                    {"account_id": a["account_id"], "currency": a["currency"]}
                    for a in _tr(reads["get_balances"]).data["accounts"]
                ]
                ctx: dict[str, Any] = {
                    "today": state["today"], "intent": intent.value, "action_to_propose": ACTION_FOR_INTENT[intent].value,
                    "user_words_understood": _display_slots(frame), "user_accounts": accounts,
                }  # fmt: skip
                if state.get("doc_summaries"):  # typed summaries only, never document text (INV-3)
                    ctx["attached_documents"] = list(state["doc_summaries"])
                    if abl.has(Ablation.NO_QUARANTINE):  # evaluation only: the document text too
                        ctx["attached_documents_text"] = redact_numbers(
                            json.dumps(state.get("doc_fields", {}), ensure_ascii=False, default=str)
                        )
                payee = state.get("payee")
                if payee:
                    ctx["resolved_payee"] = {
                        "payee_id": payee["payee_id"], "account": payee["account_no"],
                        "currency": payee["currency"], "region": payee["region"],
                    }  # fmt: skip
                if state.get("invoice_id"):
                    inv = next(
                        i
                        for i in _tr(reads["list_invoices"]).data["invoices"]
                        if i["invoice_id"] == state["invoice_id"]
                    )
                    ctx["open_invoice"] = {
                        k: inv[k]
                        for k in ("invoice_id", "amount", "currency", "due_date")
                        if k in inv
                    }  # no free text (INV-3)
                messages = build_planner_messages(ctx)
                res = await gw.complete(
                    ModelRole.PLANNER, messages, schema=PlannerOutput,
                    provider=deps.planner_provider, model=deps.planner_model,
                )  # fmt: skip
                if not isinstance(res.parsed, PlannerOutput):
                    raise PlanInvalid("no planner output")
                vplan = validate_plan(
                    res.parsed, intent=intent, resolved_payee_id=payee["payee_id"] if payee else None,
                    allowed_invoice_ids=frozenset([str(state["invoice_id"])]) if state.get("invoice_id") else frozenset(),
                )  # fmt: skip
                planner_info = {
                    "model": res.model,
                    "provider": res.provider,
                    "prompt": PROMPT_VERSION,
                    "failover_from": res.failover_from,
                }
            else:
                vplan = _read_intent_plan(state, frame, intent)
                planner_info = {"model": None, "provider": None, "prompt": "deterministic"}
        except (PlanInvalid, LLMError, ProposalError, KeyError, StopIteration) as exc:
            # ONE message per failed request: the template reply says what is wrong (a model that cannot answer is
            # "unavailable", with the buttons that still work); the error event only goes to the log and the ledger.
            log.warning("plan_failed", detail=type(exc).__name__)
            return fail(unavailable=isinstance(exc, LLMError))
        data = {
            "steps": [{"id": s.id, "kind": s.kind.value, "tool": s.tool, "args": {k: v.value for k, v in s.args.items()}, "depends_on": list(s.depends_on)} for s in vplan.plan.steps],
            "proposal_draft": vplan.draft.model_dump(mode="json") if vplan.draft else None,
            "notes": list(vplan.notes), "planner": planner_info,
        }  # fmt: skip
        em.emit(ET.PLAN, data)
        return {
            "plan": {
                "calls": [[i, t, a] for i, t, a in vplan.read_calls],
                "has_proposal": vplan.draft is not None,
                "notes": list(vplan.notes),
            },
            "next_seq": em.seq,
        }

    def _read_intent_plan(
        state: AgentState, frame: dict[str, Any], intent: IntentType
    ) -> ValidatedPlan:
        home = cfgc.home_currency.get(state["profile"]["home_region"], "NPR")
        ccy = (_slot(frame, "currency") or {}).get("value")
        amount = (_slot(frame, "amount") or {}).get("value")
        if intent is IntentType.CHECK_BALANCE:
            return default_read_plan(intent, [("get_balances", {})])
        if intent is IntentType.SPENDING_SUMMARY:
            return default_read_plan(intent, [("get_history", {"days": 30, "limit": 50})])
        if intent is IntentType.COMPARE_ROUTES:
            src = ccy or home
            dst = "NPR" if src != "NPR" else "CNY"
            quote: dict[str, Any] = {"from_currency": src, "to_currency": dst}
            if amount:
                quote["amount_minor"] = int(amount)
            route_args: dict[str, Any] = {"from_currency": src, "to_currency": dst}
            dl = (_slot(frame, "deadline") or {}).get("value")
            if isinstance(dl, dict) and dl.get("date"):
                route_args["deadline"] = dl["date"]
            calls: list[tuple[str, dict[str, Any]]] = [("get_fx_quote", quote)]
            if amount:
                route_args["amount_minor"] = int(amount)
                calls.insert(0, ("compare_routes", route_args))
            else:
                calls.insert(0, ("list_providers", {"from_currency": src, "to_currency": dst}))
            return default_read_plan(intent, calls)
        order = frame.get("currencies_in_order", [])
        quote = {"from_currency": order[0], "to_currency": order[1]}
        if amount:
            quote["amount_minor"] = int(amount)
        return default_read_plan(intent, [("get_fx_quote", quote)])

    def after_plan(state: AgentState) -> str:
        return "run_read_tools" if state.get("plan") else "emit"

    # ------------------------------------------------------------------ run_read_tools
    async def run_read_tools(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        em = _em(state, config)
        lang, deva = _lang(state)
        frame = state["frame"] or {}
        intent = IntentType(frame["intent"])
        reads = dict(state.get("reads", {}))
        calls = [(i, t, a) for i, t, a in state["plan"]["calls"]]  # type: ignore[index]
        if (
            intent is IntentType.SEND_MONEY
        ):  # reads the proposal needs, added by code, not by the model
            money = _money(state)
            if money.cross_border:
                pair = {"from_currency": money.src.value, "to_currency": money.dst.value}
                calls += [
                    ("req1", "get_fx_quote", dict(pair)),
                    ("req2", "list_providers", dict(pair)),
                ]
            if _slot(frame, "condition"):
                calls += [("req3", "get_history", {"days": 366, "limit": 100})]
        if intent is IntentType.PAY_INVOICE and state.get("invoice_id") and not state.get("docs"):
            calls += [  # the funding plan is computed by code (CP-SAT), only for a stored invoice
                ("req4", "plan_tuition_funding", {"invoice_id": state["invoice_id"]})
            ]
        ran: set[tuple[str, str]] = set()
        try:
            for step_id, tool, args in calls:
                key = (tool, repr(sorted(args.items())))
                if key in ran or (
                    tool in reads and tool in ("get_payees", "get_balances", "list_invoices")
                ):
                    continue
                reads[tool] = run_tool(state, em, step_id, tool, args).model_dump(mode="json")
                ran.add(key)
        except (ToolError, ProposalError):
            em.emit(ET.ERROR, {"reason": "tool_failed"})
            return {
                "status": "rejected",
                "reply": composer.template("planner_failed", lang, deva, packs),
                "reply_source": "template",
                "reads": reads,
                "next_seq": em.seq,
            }
        return {"reads": reads, "next_seq": em.seq}

    def _money(state: AgentState) -> MoneyPlan:
        frame = state["frame"] or {}
        reads = state["reads"]
        accounts = _tr(reads["get_balances"]).data["accounts"]
        payee = state["payee"]
        assert payee is not None
        return decide_money(
            payee_currency=payee["currency"], amount_currency=(_slot(frame, "currency") or {}).get("value"),
            home_region=state["profile"]["home_region"], accounts=accounts, corridors=cfgc,
        )  # fmt: skip

    def after_reads(state: AgentState) -> str:
        if state.get("status") == "rejected":
            return "emit"
        intent = IntentType((state["frame"] or {})["intent"])
        return "specialists" if intent in ACTION_INTENTS else "compose"

    # ------------------------------------------------------------------ specialists (default route, P1)
    async def specialists(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        em = _em(state, config)
        lang, deva = _lang(state)
        frame = state["frame"] or {}
        if (
            IntentType(frame["intent"]) is IntentType.PAY_INVOICE
            and "plan_tuition_funding" in state["reads"]
        ):
            return _funding_specialist(state, em, lang, deva)
        if IntentType(frame["intent"]) is not IntentType.SEND_MONEY:
            return {"next_seq": em.seq}
        try:
            money = _money(state)
            amount = int((_slot(frame, "amount") or {})["value"])
            route = provider = None
            if money.cross_border:
                reads = state["reads"]
                dl = (_slot(frame, "deadline") or {}).get("value")
                hours = None
                if isinstance(dl, dict) and dl.get("date"):
                    hours = max((date.fromisoformat(dl["date"]) - _today(state)).days, 0) * 24
                route, provider = compute_route(
                    money=money, amount_minor=amount, providers=_tr(reads["list_providers"]),
                    quote=_tr(reads["get_fx_quote"]), corridors=cfgc, hours_until_deadline=hours,
                )  # fmt: skip
        except ProposalError as exc:
            return _proposal_failure(exc, em, lang, deva, state)
        if route is not None:
            from corridor.agent.routing import route_summary

            em.emit(ET.ROUTE, route_summary(route, provider or ""))
        return {
            "money": {"src": money.src.value, "dst": money.dst.value, "mode": money.mode, "source_account_id": money.source_account_id},
            "route": route.model_dump(mode="json") if route else None, "route_provider": provider, "next_seq": em.seq,
        }  # fmt: skip

    def _typed_payee_prepare(
        state: AgentState,
        em: Emitter,
        frame: dict[str, Any],
        slots: dict[str, Any],
        lang: LanguageTag,
        deva: bool,
        ask: Callable[..., dict[str, Any]],
        done: Callable[..., dict[str, Any]],
    ) -> dict[str, Any]:
        """Save a payee from the user's own message (ADR 0018). The account number and the name are
        found by code in the user's words; where they came from decides the provenance. Missing or
        ambiguous parts are asked about, never guessed."""
        if frame["confidence_bps"] < 8000:
            return ask("generic", "intent")
        accounts = state.get("typed_accounts") or []
        if not accounts:
            return ask("payee_account", "payee_account")
        ref = (
            (slots.get("payee_ref") or {}).get("value")
            if isinstance(slots.get("payee_ref"), dict)
            else None
        )
        slot_text = ref.get("text") if isinstance(ref, dict) else None
        named = find_name(state["text"], slot_text)
        if named is None:
            return ask("payee_name", "payee_name")
        try:
            balances = run_tool(state, em, "pre2", "get_balances", {})
        except ToolError:
            em.emit(ET.ERROR, {"reason": "tool_failed"})
            return done(composer.template("planner_failed", lang, deva, packs), "rejected", None)
        ccy_slot = slots.get("currency")
        currency = (
            str(ccy_slot["value"]) if isinstance(ccy_slot, dict) and ccy_slot.get("value") else None
        )
        if currency is None:
            first = next(
                (
                    a
                    for a in balances.data.get("accounts", [])
                    if a.get("kind", "spending") == "spending"
                ),
                None,
            )
            currency = str(first["currency"]) if first else "AUD"
        reads = dict(state.get("reads", {}))
        reads["get_balances"] = balances.model_dump(mode="json")
        return {
            "status": "running", "reads": reads, "next_seq": em.seq,
            "add_payee_typed": {
                "name": named[0], "name_prov": named[1].value, "currency": currency,
                "hash": accounts[0]["hash"], "last4": accounts[0]["last4"],
                "account_prov": accounts[0]["provenance"],
            },
        }  # fmt: skip

    def _passport_prepare(
        state: AgentState,
        em: Emitter,
        intent: IntentType,
        lang: LanguageTag,
        deva: bool,
        style: str,
        ask: Callable[..., dict[str, Any]],
        done: Callable[..., dict[str, Any]],
    ) -> dict[str, Any]:
        """Passport requests are decided by code: the claim set comes from the stored passport and the
        user's own words (keywords), never from a model. Unclear means ask, nothing is guessed."""
        reads = dict(state.get("reads", {}))
        try:
            reads["get_balances"] = run_tool(state, em, "pre2", "get_balances", {}).model_dump(
                mode="json"
            )
            passports = run_tool(state, em, "pre4", "list_passports", {})
        except ToolError:
            em.emit(ET.ERROR, {"reason": "tool_failed"})
            return done(composer.template("planner_failed", lang, deva, packs), "rejected", None)
        today = _today(state).isoformat()
        live = [p for p in passports.data.get("passports", []) if p["expires_on"] > today]
        if intent is IntentType.CREDIT_PASSPORT_ISSUE:
            return {
                "status": "running", "reads": reads, "passport_action": {"kind": "issue"},
                "next_seq": em.seq,
            }  # fmt: skip
        if not live:
            em.emit(ET.REJECTED, {"reason": "no_passport"})
            return done(composer.template("passport_none", lang, deva, packs), "rejected", None)
        held = live[0]  # the newest unexpired passport
        claims = claims_from_text(state["text"], frozenset(held["claim_ids"]))
        if not claims:
            options = tuple(held["claim_ids"])
            return ask(
                "passport_claim", "passport_claim", options, fmt={"options": " / ".join(options)}
            )
        return {
            "status": "running", "reads": reads, "next_seq": em.seq,
            "passport_action": {
                "kind": "share", "passport_id": held["passport_id"], "claim_ids": list(claims),
                "audience": audience_from_text(state["text"]),
            },
        }  # fmt: skip

    def _funding_specialist(
        state: AgentState, em: Emitter, lang: LanguageTag, deva: bool
    ) -> dict[str, Any]:
        """Keep the funding plan (made by code in a read tool) or say that there is none."""
        res = _tr(state["reads"]["plan_tuition_funding"])
        data = res.data
        if not data.get("found"):
            return {"next_seq": em.seq}
        em.emit(
            ET.ROUTE, {"label": "funding_plan", "status": data["status"], "steps": data["steps"]}
        )
        if data["status"] != "ok" or not data["steps"]:
            reason = "plan_infeasible" if data["status"] == "infeasible" else "plan_unavailable"
            em.emit(ET.REJECTED, {"reason": reason, "invoice_id": data["invoice_id"]})
            log_ledger(
                state, em, LedgerEventType.REJECTION, {"reason": reason, "stage": "funding_plan"}
            )
            return {
                "status": "rejected",
                "reply": composer.template(
                    "funding_infeasible", lang, deva, packs, owed=data["still_owed"], due=data["due_date"]
                ),
                "reply_source": "template", "next_seq": em.seq,
            }  # fmt: skip
        return {"plan_steps": data["steps"], "plan_index": 0, "next_seq": em.seq}

    def _proposal_failure(
        exc: ProposalError, em: Emitter, lang: LanguageTag, deva: bool, state: AgentState
    ) -> dict[str, Any]:
        em.emit(ET.REJECTED, {"reason": exc.code, **exc.details})
        log_ledger(state, em, LedgerEventType.REJECTION, {"reason": exc.code, "stage": "propose"})
        if exc.code == "insufficient_funds":
            reply = composer.template(
                "insufficient_funds",
                lang,
                deva,
                packs,
                balance=exc.details["balance"],
                needed=exc.details["needed"],
            )
        elif exc.code == "no_route" or exc.code == "no_account":
            reply = composer.template(
                "no_route",
                lang,
                deva,
                packs,
                reason=str(exc.details.get("reason") or exc.details.get("currency") or exc.code),
            )
        else:
            reply = composer.template("planner_failed", lang, deva, packs)
        return {
            "status": "rejected",
            "reply": reply,
            "reply_source": "template",
            "next_seq": em.seq,
        }

    def after_specialists(state: AgentState) -> str:
        return "emit" if state.get("status") == "rejected" else "propose"

    # ------------------------------------------------------------------ propose
    async def propose(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        em = _em(state, config)
        lang, deva = _lang(state)
        frame = state["frame"] or {}
        intent = IntentType(frame["intent"])
        reads = state["reads"]
        try:
            if state.get("add_payee_doc"):
                new_doc = state["add_payee_doc"] or ""
                terms = payee_terms(state.get("doc_fields", {}).get(new_doc, {}))
                if terms is None:
                    raise ProposalError("invoice_not_found")
                build = build_add_payee(
                    user_id=state["user_id"], terms=terms, doc_hash=new_doc,
                    balances=_tr(reads["get_balances"]),
                )  # fmt: skip
            elif state.get("add_payee_typed"):
                typed = state["add_payee_typed"] or {}
                build = build_add_typed_payee(
                    user_id=state["user_id"],
                    name=(typed["name"], Provenance(typed["name_prov"])),
                    account_hash=typed["hash"], last4=typed["last4"],
                    account_prov=Provenance(typed["account_prov"]), currency=typed["currency"],
                    balances=_tr(reads["get_balances"]),
                )  # fmt: skip
            elif intent is IntentType.SEND_MONEY:
                money = _money(state)
                amount_slot = _slot(frame, "amount")
                assert amount_slot is not None
                cond = _slot(frame, "condition")
                if cond:
                    route = state.get("route")
                    send = (
                        RouteRecommendation.model_validate(route).legs[0].send_amount.amount_minor
                        if route
                        else int(amount_slot["value"])
                    )
                    result = evaluate_condition(
                        cond["value"], source_account_id=money.source_account_id, send_minor=send,
                        balances=_tr(reads["get_balances"]), payees=_tr(reads["get_payees"]), history=_tr(reads["get_history"]),
                    )  # fmt: skip
                    if not result.met:
                        em.emit(
                            ET.REJECTED, {"reason": "condition_not_met", "condition": result.reason}
                        )
                        log_ledger(
                            state, em, LedgerEventType.REJECTION,
                            {"reason": "condition_not_met", "stage": "propose"},
                        )  # fmt: skip
                        if result.reason == "insufficient" and result.currency:
                            ccy = CurrencyCode(result.currency)
                            reply = composer.template(
                                "condition_not_met", lang, deva, packs, reserve=format_minor(result.reserve_minor or 0, ccy),
                                left=format_minor(max((result.balance_minor or 0) - (result.reserve_minor or 0), 0), ccy),
                                needed=format_minor(result.needed_minor or 0, ccy),
                            )  # fmt: skip
                        else:
                            reply = composer.template("condition_unknown", lang, deva, packs)
                        return {
                            "status": "rejected",
                            "reply": reply,
                            "reply_source": "template",
                            "next_seq": em.seq,
                        }
                route_model = (
                    RouteRecommendation.model_validate(state["route"])
                    if state.get("route")
                    else None
                )
                build = build_transfer(
                    user_id=state["user_id"], money=money, amount_slot=amount_slot, payee=state["payee"] or {},
                    balances=_tr(reads["get_balances"]), route=route_model, provider_name=state.get("route_provider"),
                    deadline=(_slot(frame, "deadline") or {}).get("value"),
                    doc_hashes=tuple(state.get("docs", [])),
                    rate_date=((reads.get("get_fx_quote") or {}).get("data") or {}).get("as_of"),
                )  # fmt: skip
            elif state.get("passport_action"):
                pa = state["passport_action"] or {}
                if pa["kind"] == "issue":
                    with Session(eng) as ps:
                        preview = tuple(preview_claim_ids(ps, state["user_id"], _today(state)))
                    build = build_passport_issue(
                        user_id=state["user_id"],
                        balances=_tr(reads["get_balances"]),
                        claim_preview=preview,
                    )
                else:
                    build = build_passport_share(
                        user_id=state["user_id"], balances=_tr(reads["get_balances"]),
                        passport_id=pa["passport_id"], claim_ids=tuple(pa["claim_ids"]),
                        audience=pa["audience"], statements=CLAIM_STATEMENTS,
                    )  # fmt: skip
            elif state.get("plan_steps"):
                steps = state["plan_steps"] or []
                step = steps[int(state.get("plan_index", 0))]
                if step["type"] == "request_contribution":
                    build = build_contribution_request(
                        user_id=state["user_id"], step=step, invoice_id=state["invoice_id"] or ""
                    )
                else:
                    build = build_invoice_payment(
                        user_id=state["user_id"], invoice_id=state["invoice_id"] or "",
                        invoices=_tr(reads["list_invoices"]), payees=_tr(reads["get_payees"]),
                        balances=_tr(reads["get_balances"]), doc_hashes=tuple(state.get("docs", [])),
                        account_id=step["from_account"], pay_minor=int(step["amount_minor"]),
                    )  # fmt: skip
            else:
                doc_hash, doc = next(
                    (
                        (h, f)
                        for h, f in state.get("doc_fields", {}).items()
                        if f.get("kind") == "invoice" and f.get("matched_invoice_id") == state.get("invoice_id")
                    ),
                    (None, None),
                )  # fmt: skip
                build = build_invoice_payment(
                    user_id=state["user_id"],
                    invoice_id=state["invoice_id"] or "",
                    invoices=_tr(reads["list_invoices"]),
                    payees=_tr(reads["get_payees"]),
                    balances=_tr(reads["get_balances"]),
                    document=doc,
                    doc_hash=doc_hash,
                    doc_hashes=tuple(state.get("docs", [])),
                )
        except ProposalError as exc:
            return _proposal_failure(exc, em, lang, deva, state)
        card = proposal_card(build)
        for w in build.warnings:
            em.emit(
                ET.TAINT_FLAG,
                {
                    "field": "payee" if state.get("add_payee_doc") else "amount",
                    "provenance": card["amount_provenance"] or "untrusted",
                    "warning": w,
                },
            )
        return {
            "proposal": build.proposal.model_dump(mode="json"), "proposal_card": card,
            "proposal_hash": card["hash"], "next_seq": em.seq,
        }  # fmt: skip

    def after_propose(state: AgentState) -> str:
        return "emit" if state.get("status") == "rejected" else "policy"

    # ------------------------------------------------------------------ policy (deterministic, INV-2)
    async def policy(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        em = _em(state, config)
        lang, deva = _lang(state)
        proposal = ActionProposal.model_validate(state["proposal"])
        phash = proposal.canonical_hash()
        now = deps.now_fn()
        entries: list[LedgerEntry] = []
        with Session(eng) as s, s.begin():
            account_state = build_account_state(s, state["user_id"], now, pcfg)
            decision = evaluate_ablated(abl, proposal, account_state, pcfg, now)
            risk_json = risk_view(assess_risk(proposal, account_state, pcfg, now))

            def append(event: LedgerEventType, data: dict[str, Any]) -> None:
                entries.append(
                    ledger.append(
                        s,
                        trace_id=state["trace_id"],
                        actor=LedgerActor.AGENT,
                        event_type=event,
                        user_id=state["user_id"],
                        session_id=state["thread_id"],
                        data=data,
                        policy_version_hash=decision.policy_version_hash,
                        now=now,
                    )
                )

            append(
                LedgerEventType.PROPOSAL,
                {
                    "proposal_hash": phash, "action": proposal.action.value,
                    "amount_minor": proposal.amount.value.amount_minor if proposal.amount else None,
                    "currency": proposal.amount.value.currency.value if proposal.amount else None,
                    "payee_id": proposal.payee_id.value if proposal.payee_id else None,
                    "source_account": proposal.source_account_id.value,
                    "invoice_id": proposal.invoice_id,
                    "provenance": {
                        "amount": proposal.amount.provenance.value if proposal.amount else None,
                        "payee": proposal.payee_id.provenance.value if proposal.payee_id else None,
                    },
                    "derived_from_docs": list(proposal.derived_from_docs),
                },
            )  # fmt: skip
            append(
                LedgerEventType.POLICY_DECISION,
                {
                    "proposal_hash": phash, "decision": decision.decision.value,
                    "rule_ids": list(decision.rule_ids), "reasons": list(decision.reasons),
                    "rule_results": [list(r) for r in decision.rule_results],
                    "risk": risk_json,
                },
            )  # fmt: skip
            lock_reason = record_policy_denial(
                s, state["user_id"], decision, now, pcfg, tuple(proposal.derived_from_docs)
            )
            if lock_reason is not None:
                append(LedgerEventType.SAFETY_LOCK, {"action": "locked", "reason": lock_reason})
        for entry in entries:
            note_ledger(em, entry)
        texts = render_reasons(decision.reasons, lang, deva, packs, _tz(state, cfgc))
        level = None if decision.decision is Decision.DENY else required_level(decision).value
        em.emit(
            ET.POLICY_DECISION,
            {
                "decision": decision.decision.value, "rule_ids": list(decision.rule_ids),
                "reason_keys": list(decision.reasons), "reasons": texts,
                "policy_version_hash": decision.policy_version_hash, "proposal_hash": phash,
                "required_level": level, "risk": risk_json,
            },
        )  # fmt: skip
        if decision.decision is Decision.DENY:
            data: dict[str, Any] = {
                "reason": "policy_denied", "rule_ids": list(decision.rule_ids), "reasons": texts,
            }  # fmt: skip
            if "R-PAYEE-COOLOFF" in decision.rule_ids and proposal.payee_id is not None:
                # the card offers "ask the bank to verify this payee" (POST /payees/{id}/verification-request)
                data["actions"] = [
                    {"kind": "request_verification", "payee_id": proposal.payee_id.value}
                ]
                for key in decision.reasons:
                    if key.startswith("R-PAYEE-COOLOFF") and "@" in key:
                        until, _, cap = key.partition("@")[2].partition("|")
                        data["hold"] = {
                            "until": until,
                            "cap": format_minor(int(cap), CurrencyCode.NPR) if cap.isdigit() else cap,
                            "untrusted": key.startswith("R-PAYEE-COOLOFF.first_payment_cap_untrusted"),
                        }  # fmt: skip
                        break
            em.emit(ET.REJECTED, data)
            return {
                "status": "rejected",
                "reply": composer.template(
                    "policy_denied", lang, deva, packs, reasons=" ".join(texts)
                ),
                "reply_source": "template",
                "decision": None,
                "next_seq": em.seq,
            }
        if decision.decision is Decision.ALLOW:  # policy never allows an action; fail closed
            em.emit(ET.ERROR, {"reason": "unexpected_policy_decision"})
            return {
                "status": "rejected",
                "reply": composer.template("planner_failed", lang, deva, packs),
                "reply_source": "template",
                "next_seq": em.seq,
            }
        return {
            "decision": {
                "decision": decision.decision.value, "rule_ids": list(decision.rule_ids),
                "reasons": texts, "reason_keys": list(decision.reasons),
            },
            "required_level": level,
            "risk": risk_json,
            "next_seq": em.seq,
        }  # fmt: skip

    def after_policy(state: AgentState) -> str:
        return "emit" if state.get("status") == "rejected" else "request_confirmation"

    # ------------------------------------------------------------------ confirmation (interrupt)
    async def request_confirmation(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        em = _em(state, config)
        lang, deva = _lang(state)
        card = state["proposal_card"] or {}
        decision = state.get("decision") or {}
        steps = state.get("plan_steps") or []
        idx = int(state.get("plan_index", 0))
        prompt = (
            composer.template("funding_plan_step", lang, deva, packs, n=idx + 1, total=len(steps))
            if len(steps) > 1
            else composer.template("proposal_ready", lang, deva, packs)
        )
        em.emit(
            ET.CONFIRM_REQUEST,
            {
                **card,
                "proposal_hash": state["proposal_hash"],
                "required_level": state.get("required_level"),
                "policy_reasons": decision.get("reasons", []),
                "rule_ids": decision.get("rule_ids", []),
                "prompt": prompt,
                **({"plan_step": {"n": idx + 1, "total": len(steps)}} if len(steps) > 1 else {}),
            },
        )
        return {
            "status": "awaiting",
            "reply": prompt,
            "reply_source": "template",
            "next_seq": em.seq,
        }

    async def await_confirmation(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        # nothing may happen before interrupt(): on resume this node starts again from its first line
        answer = interrupt(
            {"proposal_hash": state.get("proposal_hash"), "trace_id": state.get("trace_id")}
        )
        em = _em(state, config)
        lang, deva = _lang(state)
        if not isinstance(answer, dict) or answer.get("action") == "cancel":
            em.emit(
                ET.REJECTED,
                {"reason": "user_cancelled", "proposal_hash": state.get("proposal_hash")},
            )
            log_ledger(
                state, em, LedgerEventType.PROPOSAL_DISCARDED,
                {"proposal_hash": state.get("proposal_hash"), "reason": "user_cancelled"},
                LedgerActor.USER,
            )  # fmt: skip
            return {
                "status": "cancelled",
                "reply": composer.template("cancelled", lang, deva, packs),
                "reply_source": "template",
                "pending": None,
                "next_seq": em.seq,
            }
        sent = answer.get("proposal_hash")
        grant_id = answer.get("grant_id")
        if abl.has(Ablation.NO_GRANTS) and answer.get("action") == "confirm":
            # evaluation only: "yes" confirms whatever is pending, with no hash binding
            return {
                "confirmed_hash": state.get("proposal_hash"),
                "grant_id": "grant_ablated",
                "next_seq": em.seq,
            }
        if (
            answer.get("action") != "confirm"
            or not sent
            or sent != state.get("proposal_hash")
            or not isinstance(grant_id, str)
        ):
            reason = "hash_mismatch" if sent else "hash_missing"
            em.emit(ET.REJECTED, {"reason": reason})
            log_ledger(
                state, em, LedgerEventType.CONFIRMATION_REJECTED,
                {"proposal_hash": state.get("proposal_hash"), "sent_hash": sent, "reason": reason},
                LedgerActor.USER,
            )  # fmt: skip
            count_failed_attempt(state, em)
            return {
                "status": "rejected",
                "reply": composer.template("hash_mismatch", lang, deva, packs),
                "reply_source": "template",
                "pending": None,
                "next_seq": em.seq,
            }
        return {"confirmed_hash": sent, "grant_id": grant_id, "next_seq": em.seq}

    def after_confirmation(state: AgentState) -> str:
        return "execute" if state.get("confirmed_hash") else "emit"

    # ------------------------------------------------------------------ execute (corridor.executor)
    async def execute(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        em = _em(state, config)
        lang, deva = _lang(state)
        proposal = ActionProposal.model_validate(state["proposal"])
        try:
            result = await asyncio.to_thread(
                executor.execute, proposal, state["grant_id"] or "", user_id=state["user_id"],
                now=deps.now_fn(), trace_id=state["trace_id"], session_id=state["thread_id"],
            )  # fmt: skip
        except ExecutionRejected as exc:
            em.emit(ET.REJECTED, {"reason": exc.reason, "rule_ids": list(exc.rule_ids)})
            if exc.reason == "insufficient_funds":
                key, fmt = "insufficient_funds", {"balance": "-", "needed": _needed(proposal)}
            elif exc.reason == "policy_denied":
                key, fmt = "policy_denied", {"reasons": ""}
            elif exc.reason.startswith("grant_"):
                key, fmt = "grant_failed", {}
            elif exc.reason == "idempotency_key_reuse":
                key, fmt = "already_processed", {}
            else:
                key, fmt = "execution_failed", {"reason": exc.reason}
            return {
                "status": "rejected",
                "reply": composer.template(key, lang, deva, packs, **fmt),
                "reply_source": "template",
                "next_seq": em.seq,
            }
        if isinstance(result, PassportDone):  # a credential was issued or shared; no money moved
            stm = [CLAIM_STATEMENTS.get(c, c) for c in result.claim_ids]
            data: dict[str, Any] = {
                "action": "issue_passport" if result.kind == "issued" else "share_passport",
                "passport_id": result.passport_id, "claims": list(result.claim_ids),
                "proposal_hash": state["confirmed_hash"], "rule_ids": list(result.rule_ids),
            }  # fmt: skip
            if result.presentation is not None:
                data["presentation"] = result.presentation
                data["audience"] = result.audience
            em.emit(ET.EXECUTED, data)
            if result.kind == "issued":
                reply = composer.template(
                    "passport_issued", lang, deva, packs, claims="; ".join(stm),
                    until=result.expires_at[:10],
                )  # fmt: skip
            else:
                reply = composer.template(
                    "passport_shared", lang, deva, packs, claims="; ".join(stm),
                    audience=result.audience or "",
                )  # fmt: skip
            return {
                "status": "passport_done", "reply": reply, "reply_source": "template",
                "pending": None, "next_seq": em.seq,
            }  # fmt: skip
        if isinstance(result, ContributionRequested):  # a request was stored; nothing moved
            steps = state.get("plan_steps") or []
            step = steps[int(state.get("plan_index", 0))] if steps else {}
            reply = composer.template(
                "contribution_requested", lang, deva, packs,
                amount=step.get("request", "-"), arrives=step.get("arrives", "-"),
            )  # fmt: skip
            reply = _with_plan_tail(state, reply, lang, deva)
            em.emit(
                ET.EXECUTED,
                {
                    "request_id": result.request_id,
                    "action": "request_contribution",
                    "amount": step.get("request"),
                    "proposal_hash": state["confirmed_hash"],
                    "rule_ids": list(result.rule_ids),
                },
            )
            return {
                "status": "contribution_requested", "reply": reply, "reply_source": "template",
                "pending": None, "next_seq": em.seq,
            }  # fmt: skip
        if isinstance(result, PayeeAdded):  # a payee was saved, held for 24 hours; no money moved
            until = format_local_time(
                result.hold_until.isoformat(timespec="minutes"), _tz(state, cfgc), deva=deva
            )
            if result.provenance == "user_typed":  # the user typed it: a cap, not a full hold
                reply = composer.template(
                    "payee_saved_typed", lang, deva, packs, name=result.display_name,
                    account=result.account_masked, until=until,
                )  # fmt: skip
            else:
                warning = " ".join(
                    render_reasons(("R-PAYEE-ADD.payee_add_untrusted_warning",), lang, deva, packs)
                )
                reply = composer.template(
                    "payee_saved_held", lang, deva, packs, name=result.display_name,
                    account=result.account_masked, until=until,
                ) + " " + warning  # fmt: skip
            return {
                "status": "payee_added", "reply": reply, "reply_source": "template",
                "pending": None, "next_seq": em.seq,
                "reply_extra": {
                    "hold": {
                        "until": result.hold_until.isoformat(), "cap": "",
                        "untrusted": result.provenance != "user_typed",
                    },
                    "actions": [{"kind": "request_verification", "payee_id": result.payee_id}],
                },
            }  # fmt: skip
        # The money has moved. Nothing below may turn this into a "nothing was sent" failure (INV-6, INV-10).
        try:
            ccy = CurrencyCode(result.currency)
            payee = (state.get("payee") or {}).get("display_name") or result.payee_id
            em.emit(
                ET.EXECUTED,
                {
                    "transfer_id": result.transfer_id, "amount": format_minor(result.amount_minor, ccy), "payee": payee,
                    "balance_after": format_minor(result.balance_after_minor, ccy), "proposal_hash": state["confirmed_hash"],
                    "replayed": result.replayed, "rule_ids": list(result.rule_ids),
                },
            )  # fmt: skip
            reply, source = await composer.compose_executed_reply(
                gw, language=lang, deva=deva, amount=format_minor(result.amount_minor, ccy), payee=str(payee),
                balance=format_minor(result.balance_after_minor, ccy), packs=packs,
            )  # fmt: skip
        except Exception as exc:
            log.error("post_commit_receipt_failed", error=type(exc).__name__)
            em.emit(
                ET.EXECUTED,
                {"transfer_id": result.transfer_id, "proposal_hash": state["confirmed_hash"]},
            )
            reply, source = composer.template("executed_fallback", lang, deva, packs), "template"
        reply = _with_plan_tail(state, reply, lang, deva)
        return {
            "status": "executed",
            "reply": reply,
            "reply_source": source,
            "pending": None,
            "exec_facts": {
                "amount_minor": result.amount_minor, "currency": result.currency,
                "balance_after_minor": result.balance_after_minor,
            },
            "next_seq": em.seq,
        }  # fmt: skip

    def _with_plan_tail(state: AgentState, reply: str, lang: LanguageTag, deva: bool) -> str:
        """After the last step of a plan that still waits for family money, say what is left to pay."""
        steps = state.get("plan_steps") or []
        if not steps or int(state.get("plan_index", 0)) + 1 < len(steps):
            return reply
        data = _tr(state["reads"]["plan_tuition_funding"]).data
        if not any(st["type"] == "request_contribution" for st in steps):
            return reply
        tail = composer.template(
            "plan_done_waiting",
            lang,
            deva,
            packs,
            paid=data["paid_now"],
            rest=data["after_family_arrives"],
            due=data["due_date"],
        )
        return f"{reply} {tail}"

    def _needed(proposal: ActionProposal) -> str:
        if proposal.amount is None:
            return "-"
        return format_minor(proposal.amount.value.amount_minor, proposal.amount.value.currency)

    def after_execute(state: AgentState) -> str:
        steps = state.get("plan_steps") or []
        if state.get("status") in ("executed", "contribution_requested") and int(
            state.get("plan_index", 0)
        ) + 1 < len(steps):
            return "next_step"  # the next payment of the funding plan gets its own card and grant
        return "ground" if state.get("status") in ("executed", "contribution_requested") else "emit"

    async def next_step(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        """Move to the next step of a funding plan. Nothing carries over: the next step is built,
        decided by policy, shown and confirmed on its own (INV-5)."""
        em = _em(state, config)
        return {
            "plan_index": int(state.get("plan_index", 0)) + 1, "status": "running", "proposal": None,
            "proposal_card": None, "proposal_hash": None, "confirmed_hash": None, "grant_id": None,
            "decision": None, "required_level": None, "next_seq": em.seq,
        }  # fmt: skip

    # ------------------------------------------------------------------ compose / ground / emit
    def shown_reads(state: AgentState) -> dict[str, ToolResult]:
        """The tool results the reply may talk about (the same set the composer sees)."""
        intent = IntentType((state["frame"] or {})["intent"])
        reads = {
            k: _tr(v)
            for k, v in state.get("reads", {}).items()
            if k not in ("get_payees",) or intent in ACTION_INTENTS
        }
        keep = {
            IntentType.CHECK_BALANCE: ("get_balances",), IntentType.SPENDING_SUMMARY: ("get_history",),
            IntentType.COMPARE_ROUTES: ("compare_routes", "list_providers", "get_fx_quote"), IntentType.CONVERT_CURRENCY: ("get_fx_quote",),
        }.get(intent, ())  # fmt: skip
        return {k: v for k, v in reads.items() if k in keep}

    async def compose(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        lang, deva = _lang(state)
        reply, source = await composer.compose_read_reply(
            gw,
            request_text=state.get("model_text", state["text"]),
            language=lang,
            deva=deva,
            results=shown_reads(state),
            packs=packs,
        )
        return {"status": "done", "reply": reply, "reply_source": source, "pending": None}

    def build_registry(state: AgentState) -> ToolResultRegistry:
        """Every number the reply may state: tool facts, the user's typed values, the receipt."""
        reg = ToolResultRegistry()
        if state.get("status") == "done":  # only the results the reply may discuss
            for res in shown_reads(state).values():
                reg.add_tool_result(res)
        frame = state.get("frame") or {}
        amount, ccy = _slot(frame, "amount"), _slot(frame, "currency")
        if (
            amount
            and isinstance(amount.get("value"), int)
            and ccy
            and amount.get("provenance") == "user_typed"
        ):
            reg.add_user_money(int(amount["value"]), str(ccy["value"]))
        deadline = (_slot(frame, "deadline") or {}).get("value")
        if isinstance(deadline, dict) and deadline.get("date"):
            reg.add_user_date(date.fromisoformat(deadline["date"]))
        if state.get("plan_steps") and "plan_tuition_funding" in state.get("reads", {}):
            reg.add_tool_result(_tr(state["reads"]["plan_tuition_funding"]))
        ex = state.get("exec_facts")
        if ex:
            reg.add_money(int(ex["amount_minor"]), ex["currency"])
            reg.add_money(int(ex["balance_after_minor"]), ex["currency"])
        return reg

    async def ground(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        """Block any number in the reply that is not a registered fact (INV-9)."""
        reply = state.get("reply") or ""
        if not reply:
            return {}
        em = _em(state, config)
        if abl.has(Ablation.NO_GROUNDING):  # evaluation only: the reply is shown unchecked
            em.emit(ET.GROUNDING, {"outcome": "skipped", "numbers_checked": 0, "attempts": []})
            return {"next_seq": em.seq}
        lang, deva = _lang(state)
        reg = build_registry(state)
        results = shown_reads(state) if state.get("status") == "done" else {}

        executed = state.get("status") in ("executed", "contribution_requested")

        async def regenerate(bad: tuple[str, ...]) -> str | None:
            if executed or state.get("reply_source") != "composer":
                return None
            text, source = await composer.compose_read_reply(
                gw, request_text=state.get("model_text", state["text"]), language=lang, deva=deva,
                results=results, packs=packs, strict=bad,
            )  # fmt: skip
            return text if source == "composer" else None

        def fallback() -> str:
            if executed:  # the money has moved: never tell the user it was not read
                return composer.template("executed_fallback", lang, deva, packs)
            return (
                composer.read_lines(results)
                if results
                else composer.template("read_unavailable", lang, deva, packs)
            )

        final, outcome, verdict, attempts = await ground_reply(
            reply,
            reg,
            regenerate=regenerate,
            fallback=fallback,
            safe=SAFE_EXECUTED if executed else None,
        )
        data = {
            "outcome": outcome, "numbers_checked": verdict.checked,
            "attempts": [
                {"ok": a.ok, "ungrounded": list(a.ungrounded)[:8]} for a in attempts
            ],
            "facts": {"tool": reg.tool_facts, "user": reg.user_facts},
        }  # fmt: skip
        em.emit(ET.GROUNDING, data)
        if outcome != "pass":
            log_ledger(
                state, em, LedgerEventType.GROUNDING_FAILURE,
                {"outcome": outcome, "ungrounded": [list(a.ungrounded)[:8] for a in attempts]},
            )  # fmt: skip
        return {
            "reply": final,
            "reply_source": "template" if outcome == "templated" else state.get("reply_source", ""),
            "grounding": data,
            "next_seq": em.seq,
        }

    async def emit_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        em = _em(state, config)
        if state.get("status") != "clarify" and state.get("reply"):
            em.emit(
                ET.MESSAGE,
                {
                    "text": state["reply"],
                    "source": state.get("reply_source", ""),
                    "status": state.get("status"),
                    **(state.get("reply_extra") or {}),
                },
            )
        return {"next_seq": em.seq, "reply_extra": None}

    g = StateGraph(AgentState)
    for name, fn in {
        "ingest": ingest, "read_untrusted": read_untrusted, "parse": parse_node, "clarify": clarify, "plan": plan,
        "run_read_tools": run_read_tools, "specialists": specialists, "propose": propose, "policy": policy,
        "request_confirmation": request_confirmation, "await_confirmation": await_confirmation, "execute": execute,
        "next_step": next_step, "compose": compose, "ground": ground, "emit": emit_node,
    }.items():  # fmt: skip
        g.add_node(name, _guarded(name, fn))
    g.set_entry_point("ingest")
    g.add_conditional_edges(
        "ingest",
        after_ingest,
        {"emit": "emit", "read_untrusted": "read_untrusted", "parse": "parse"},
    )
    g.add_conditional_edges(
        "read_untrusted", after_read_untrusted, {"parse": "parse", "emit": "emit"}
    )
    g.add_edge("parse", "clarify")
    g.add_conditional_edges(
        "clarify", after_clarify, {"plan": "plan", "propose": "propose", "emit": "emit"}
    )
    g.add_conditional_edges(
        "plan", after_plan, {"run_read_tools": "run_read_tools", "emit": "emit"}
    )
    g.add_conditional_edges(
        "run_read_tools",
        after_reads,
        {"specialists": "specialists", "compose": "compose", "emit": "emit"},
    )
    g.add_conditional_edges(
        "specialists", after_specialists, {"propose": "propose", "emit": "emit"}
    )
    g.add_conditional_edges("propose", after_propose, {"policy": "policy", "emit": "emit"})
    g.add_conditional_edges(
        "policy", after_policy, {"request_confirmation": "request_confirmation", "emit": "emit"}
    )
    g.add_edge("request_confirmation", "await_confirmation")
    g.add_conditional_edges(
        "await_confirmation", after_confirmation, {"execute": "execute", "emit": "emit"}
    )
    g.add_conditional_edges(
        "execute", after_execute, {"ground": "ground", "emit": "emit", "next_step": "next_step"}
    )
    g.add_edge("next_step", "propose")
    g.add_edge("compose", "ground")
    g.add_edge("ground", "emit")
    g.add_edge("emit", END)
    return g.compile(checkpointer=checkpointer)


def _guarded(name: str, fn: Any) -> Any:
    """Fail closed: an unexpected exception in a node ends the turn with a safe message (INV-10).

    LangGraph's interrupt is an exception too (GraphInterrupt) and must pass through untouched."""
    from langgraph.errors import GraphBubbleUp

    async def wrapper(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        try:
            return await fn(state, config)  # type: ignore[no-any-return]
        except GraphBubbleUp:
            raise
        except Exception as exc:
            log.error("agent_node_failed", node=name, error=type(exc).__name__)
            em = _em(state, config)
            em.emit(ET.ERROR, {"reason": "internal_error", "node": name})
            return {
                "status": "rejected", "reply_source": "system", "next_seq": em.seq, "pending": None,
                "reply": "Something went wrong and nothing was sent. Please try again.",
            }  # fmt: skip

    return wrapper
