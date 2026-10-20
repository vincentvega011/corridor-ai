from datetime import date, datetime, timedelta
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

import corridor.contracts as c
from contract_builders import HASH_A, make_money, make_proposal, make_route
from corridor.contracts import (
    AgentEvent,
    AgentEventType,
    ConfirmationGrant,
    ConfirmationLevel,
    CurrencyCode,
    InvoiceDoc,
    LedgerEntry,
    MessageDoc,
    Money,
    NumericFact,
    ParserOutput,
    Plan,
    PlannerOutput,
    PlanStep,
    PolicyDecision,
    ProposalDraft,
    Provenance,
    ReaderOutput,
    RiskAssessment,
    StatementDoc,
    StepKind,
    Tainted,
    ToolResult,
    ToolSpec,
)


def _public_models() -> list[type[BaseModel]]:
    out = []
    for name in c.__all__:
        obj = getattr(c, name)
        if isinstance(obj, type) and issubclass(obj, BaseModel):
            out.append(obj)
    return out


@pytest.mark.parametrize("model", _public_models(), ids=lambda m: m.__name__)
def test_all_models_frozen_and_forbid_extra(model: type[BaseModel]) -> None:
    assert model.model_config.get("frozen") is True
    assert model.model_config.get("extra") == "forbid"


def test_instance_is_immutable() -> None:
    m = make_money()
    with pytest.raises(ValidationError):
        m.amount_minor = 5  # type: ignore[misc]
    p = make_proposal()
    with pytest.raises(ValidationError):
        p.idempotency_key = "other"  # type: ignore[misc]


# --- documents -------------------------------------------------------------------------
def _claims_system(v: Any) -> Tainted[Any]:
    return Tainted[Any](value=v, provenance=Provenance.SYSTEM, source_ref="x")


def test_invoice_forces_untrusted() -> None:
    doc = InvoiceDoc(
        source_doc_hash=HASH_A,
        issuer=_claims_system("Uni"),
        amount=Tainted[Money](value=make_money(), provenance=Provenance.USER_CONFIRMED),
        beneficiary_account=_claims_system("123"),
    )
    assert doc.issuer.provenance is Provenance.UNTRUSTED
    assert doc.amount is not None and doc.amount.provenance is Provenance.UNTRUSTED
    assert doc.beneficiary_account is not None
    assert doc.beneficiary_account.provenance is Provenance.UNTRUSTED
    assert doc.beneficiary_account.source_ref == "x"


def test_invoice_from_dict_claiming_system() -> None:
    doc = InvoiceDoc.model_validate(
        {"source_doc_hash": HASH_A, "issuer": {"value": "U", "provenance": "system"}}
    )
    assert doc.issuer.provenance is Provenance.UNTRUSTED


def test_message_markers_forced_untrusted() -> None:
    doc = MessageDoc(
        source_doc_hash=HASH_A,
        sender_label=_claims_system("cousin"),
        urgency_markers=(_claims_system("urgent"), _claims_system("now")),
        injection_suspected=True,
    )
    assert all(m.provenance is Provenance.UNTRUSTED for m in doc.urgency_markers)
    assert doc.injection_suspected


def test_statement_forces_untrusted() -> None:
    doc = StatementDoc(
        source_doc_hash=HASH_A,
        issuer=_claims_system("Bank"),
        period_start=Tainted[date](value=date(2026, 1, 1), provenance=Provenance.SYSTEM),
        entry_count=Tainted[int](value=3, provenance=Provenance.SYSTEM),
    )
    assert doc.period_start is not None
    assert doc.period_start.provenance is Provenance.UNTRUSTED
    assert doc.entry_count is not None and doc.entry_count.provenance is Provenance.UNTRUSTED


def test_free_text_capped_at_120() -> None:
    with pytest.raises(ValidationError):
        InvoiceDoc(source_doc_hash=HASH_A, issuer=_claims_system("x" * 121))
    InvoiceDoc(source_doc_hash=HASH_A, issuer=_claims_system("x" * 120))


def test_doc_requires_valid_hash() -> None:
    with pytest.raises(ValidationError):
        InvoiceDoc(source_doc_hash="nothex", issuer=_claims_system("U"))


# --- llm outputs -----------------------------------------------------------------------
def test_llm_outputs_reject_provenance_field() -> None:
    with pytest.raises(ValidationError):
        ParserOutput.model_validate(
            {"language": "en", "intent": "help", "confidence_bps": 9000, "provenance": "system"}
        )
    with pytest.raises(ValidationError):
        ProposalDraft.model_validate(
            {"action": "transfer", "source_account_ref": "a", "provenance": "system"}
        )
    with pytest.raises(ValidationError):
        PlannerOutput.model_validate({"steps": [], "provenance": "system"})
    with pytest.raises(ValidationError):
        ReaderOutput.model_validate(
            {
                "doc_type": "invoice",
                "invoice": {"issuer": "U"},
                "provenance": "system",
            }
        )


def test_llm_nested_provenance_rejected() -> None:
    with pytest.raises(ValidationError):
        ReaderOutput.model_validate(
            {"doc_type": "invoice", "invoice": {"issuer": "U", "provenance": "system"}}
        )
    with pytest.raises(ValidationError):
        PlannerOutput.model_validate(
            {
                "steps": [
                    {
                        "id": "s1",
                        "kind": "READ",
                        "tool": "get_balances",
                        "args": {"Provenance": "x"},
                    }
                ]
            }
        )


def test_llm_outputs_have_no_trust_fields() -> None:
    for model in (ParserOutput, PlannerOutput, ProposalDraft, ReaderOutput):
        assert "provenance" not in model.model_fields


def test_proposal_draft_has_no_user_or_idempotency_control() -> None:
    fields = set(ProposalDraft.model_fields)
    assert not fields & {"user_id", "idempotency_key", "risk", "route"}


def test_proposal_draft_amount_pair_and_floats() -> None:
    with pytest.raises(ValidationError):
        ProposalDraft(action=c.ActionType.TRANSFER, source_account_ref="a", amount_minor=5)
    with pytest.raises(ValidationError):
        ProposalDraft.model_validate(
            {
                "action": "transfer",
                "source_account_ref": "a",
                "amount_minor": 5.5,
                "currency": "AUD",
            }
        )


def test_parser_output_rejects_float_slot() -> None:
    with pytest.raises(ValidationError):
        ParserOutput.model_validate(
            {
                "language": "en",
                "intent": "send_money",
                "confidence_bps": 10000,
                "slots": {"amount": 2.5},
            }
        )
    ok = ParserOutput.model_validate(
        {
            "language": "ne_latn",
            "intent": "send_money",
            "confidence_bps": 9000,
            "slots": {"amount": 20000},
        }
    )
    assert ok.slots[c.SlotName.AMOUNT] == 20000


def test_reader_output_exactly_one_doc() -> None:
    with pytest.raises(ValidationError):
        ReaderOutput.model_validate({"doc_type": "invoice"})
    with pytest.raises(ValidationError):
        ReaderOutput.model_validate({"doc_type": "message", "invoice": {"issuer": "U"}})
    ReaderOutput.model_validate(
        {"doc_type": "invoice", "invoice": {"issuer": "U"}, "injection_suspected": True}
    )


# --- intent ----------------------------------------------------------------------------
def test_intent_frame_slot_provenance() -> None:
    ok = c.IntentFrame(
        language=c.LanguageTag.NE_LATN,
        intent=c.IntentType.SEND_MONEY,
        slots={c.SlotName.AMOUNT: Tainted[Any](value=20000, provenance=Provenance.USER_TYPED)},
        confidence_bps=9000,
        parser="local",
    )
    assert ok.intent is c.IntentType.SEND_MONEY
    for bad in (Provenance.UNTRUSTED, Provenance.SYSTEM, Provenance.USER_CONFIRMED):
        with pytest.raises(ValidationError):
            c.IntentFrame(
                language=c.LanguageTag.EN,
                intent=c.IntentType.SEND_MONEY,
                slots={c.SlotName.AMOUNT: Tainted[Any](value=1, provenance=bad)},
                confidence_bps=5000,
                parser="cloud",
            )


def test_intent_set_is_closed() -> None:
    assert len(c.IntentType) == 15
    with pytest.raises(ValidationError):
        c.IntentFrame(language="en", intent="wire_all", confidence_bps=5000, parser="local")  # type: ignore[arg-type]


# --- plan ------------------------------------------------------------------------------
def _step(i: str, deps: tuple[str, ...] = ()) -> PlanStep:
    return PlanStep(id=i, kind=StepKind.READ, tool="get_balances", depends_on=deps)


def test_plan_ok_diamond() -> None:
    Plan(steps=(_step("a"), _step("b", ("a",)), _step("c", ("a",)), _step("d", ("b", "c"))))
    Plan(steps=())


def test_plan_rejects_cycle() -> None:
    with pytest.raises(ValidationError, match="cycle"):
        Plan(steps=(_step("a", ("b",)), _step("b", ("a",))))
    with pytest.raises(ValidationError, match="itself"):
        Plan(steps=(_step("a", ("a",)),))
    with pytest.raises(ValidationError, match="cycle"):
        Plan(steps=(_step("a"), _step("b", ("a", "d")), _step("c", ("b",)), _step("d", ("c",))))


def test_plan_rejects_dangling_duplicate_and_oversize() -> None:
    with pytest.raises(ValidationError, match="unknown"):
        Plan(steps=(_step("a", ("zzz",)),))
    with pytest.raises(ValidationError, match="unique"):
        Plan(steps=(_step("a"), _step("a")))
    Plan(steps=tuple(_step(f"s{i}") for i in range(12)))
    with pytest.raises(ValidationError, match="maximum"):
        Plan(steps=tuple(_step(f"s{i}") for i in range(13)))


# --- action ----------------------------------------------------------------------------
def test_proposal_shape_rules() -> None:
    with pytest.raises(ValidationError):
        make_proposal(amount=None)
    with pytest.raises(ValidationError):
        make_proposal(payee_id=None)
    with pytest.raises(ValidationError):
        make_proposal(amount=Tainted[Money](value=make_money(0), provenance=Provenance.USER_TYPED))
    with pytest.raises(ValidationError):
        make_proposal(action=c.ActionType.PAY_INVOICE)
    make_proposal(action=c.ActionType.PAY_INVOICE, invoice_id="inv1")
    make_proposal(action=c.ActionType.ISSUE_PASSPORT, amount=None, payee_id=None, route=None)


def test_proposal_json_roundtrip_keeps_hash() -> None:
    p = make_proposal()
    q = c.ActionProposal.model_validate_json(p.model_dump_json())
    assert q == p and q.canonical_hash() == p.canonical_hash()


def test_hash_ignores_provenance() -> None:
    p = make_proposal()
    q = make_proposal(
        payee_id=Tainted[str](value="payee1", provenance=Provenance.USER_CONFIRMED),
        source_account_id=Tainted[str](value="acc1", provenance=Provenance.UNTRUSTED),
    )
    assert p.canonical_hash() == q.canonical_hash()


# --- route / risk / tools --------------------------------------------------------------
def test_route_consistency() -> None:
    r = make_route()
    with pytest.raises(ValidationError):
        r.model_copy(update={}).__class__(
            **{**r.model_dump(), "delivered": make_money(1, CurrencyCode.NPR)}
        )
    with pytest.raises(ValidationError):
        c.RouteRecommendation(**{**r.model_dump(), "legs": ()})


def test_risk_bounds_and_reasons() -> None:
    reasons = tuple(c.RiskReason(feature=f"f{i}", text="t") for i in range(3))
    RiskAssessment(score_bps=5000, level=c.RiskLevel.HIGH, top_reasons=reasons, model_id="m")
    with pytest.raises(ValidationError):
        RiskAssessment(score_bps=10001, level=c.RiskLevel.LOW, model_id="m")
    with pytest.raises(ValidationError):
        RiskAssessment(score_bps=0.5, level=c.RiskLevel.LOW, model_id="m")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        RiskAssessment(
            score_bps=5000,
            level=c.RiskLevel.HIGH,
            top_reasons=(*reasons, c.RiskReason(feature="x", text="y")),
            model_id="m",
        )


def test_tool_kind_is_read_only() -> None:
    assert [k.value for k in c.ToolKind] == ["read"]
    spec = ToolSpec(name="get_balances", description="Balances", args_schema={"type": "object"})
    assert spec.kind is c.ToolKind.READ
    with pytest.raises(ValidationError):
        ToolSpec(name="x", description="y", kind="write")  # type: ignore[arg-type]


def test_numeric_fact_shapes() -> None:
    NumericFact(label="fee", kind=c.FactKind.MONEY, amount_minor=5, currency=CurrencyCode.AUD)
    NumericFact(label="r", kind=c.FactKind.RATE, rate="85.1")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        NumericFact(label="fee", kind=c.FactKind.MONEY, amount_minor=5)
    with pytest.raises(ValidationError):
        NumericFact(label="n", kind=c.FactKind.COUNT, count=1, on=date(2026, 1, 1))
    with pytest.raises(ValidationError):
        NumericFact(label="n", kind=c.FactKind.COUNT, count=True)
    ToolResult(tool_call_id="t1", tool="get_balances", data={}, facts=())


# --- policy / confirm / ledger / events ------------------------------------------------
def test_policy_decision_needs_rule() -> None:
    with pytest.raises(ValidationError):
        PolicyDecision(decision=c.Decision.ALLOW, rule_ids=(), policy_version_hash=HASH_A)
    PolicyDecision(decision=c.Decision.DENY, rule_ids=("R-LOCK",), policy_version_hash=HASH_A)


def test_combine_basics() -> None:
    D = c.Decision
    assert c.combine([D.ALLOW, D.REQUIRE_CONFIRM]) is D.REQUIRE_CONFIRM
    assert c.combine([D.REQUIRE_MFA, D.REQUIRE_CONFIRM, D.ALLOW]) is D.REQUIRE_MFA
    assert c.combine([D.ALLOW, D.DENY, D.REQUIRE_MFA]) is D.DENY
    assert c.combine([]) is D.DENY
    assert D.combine([D.ALLOW]) is D.ALLOW


def test_grant_ttl_and_expiry(now: datetime) -> None:
    g = ConfirmationGrant(
        grant_id="g",
        proposal_hash=HASH_A,
        user_id="u",
        level=ConfirmationLevel.CONFIRM,
        issued_at=now,
        expires_at=now + timedelta(seconds=120),
    )
    assert not g.is_expired(now + timedelta(seconds=119))
    assert g.is_expired(now + timedelta(seconds=120))
    assert not g.used
    with pytest.raises(ValueError):
        g.is_expired(datetime(2026, 1, 1))
    for bad in (121, 119, 3600):
        with pytest.raises(ValidationError):
            ConfirmationGrant(
                grant_id="g",
                proposal_hash=HASH_A,
                user_id="u",
                level=ConfirmationLevel.MFA,
                issued_at=now,
                expires_at=now + timedelta(seconds=bad),
            )
    with pytest.raises(ValidationError):
        ConfirmationGrant(
            grant_id="g",
            proposal_hash=HASH_A,
            user_id="u",
            level=ConfirmationLevel.MFA,
            issued_at=datetime(2026, 1, 1),
            expires_at=datetime(2026, 1, 1, 0, 2),
        )


def _eh(
    now: datetime,
    seq: int = 1,
    trace: str = "t",
    actor: c.LedgerActor = c.LedgerActor.SYSTEM,
    ev: c.LedgerEventType = c.LedgerEventType.PROPOSAL,
) -> str:
    return c.compute_entry_hash(c.GENESIS_PREV_HASH, seq, now, trace, actor, ev, HASH_A, HASH_A)


def test_entry_hash_covers_every_column(now: datetime) -> None:
    base = _eh(now)
    assert _eh(now, trace="u") != base
    assert _eh(now, actor=c.LedgerActor.USER) != base
    assert _eh(now, ev=c.LedgerEventType.EXECUTION) != base
    assert _eh(now + timedelta(seconds=1)) != base
    assert c.payload_hash_of("{}") == __import__("hashlib").sha256(b"{}").hexdigest()
    assert len(c.payload_hash_of("{}")) == 64


def test_ledger_entry_and_hash(now: datetime) -> None:
    h = _eh(now, seq=1)
    assert len(h) == 64
    assert h != _eh(now, seq=2)
    e = LedgerEntry(
        seq=1,
        ts=now,
        trace_id="t",
        actor=c.LedgerActor.SYSTEM,
        event_type=c.LedgerEventType.PROPOSAL,
        payload_canonical_json="{}",
        payload_hash=HASH_A,
        policy_version_hash=HASH_A,
        prev_hash=c.GENESIS_PREV_HASH,
        entry_hash=h,
    )
    assert LedgerEntry.model_validate_json(e.model_dump_json()) == e
    with pytest.raises(ValidationError):
        LedgerEntry(**{**e.model_dump(), "seq": 0})


def test_event_types_cover_spec(now: datetime) -> None:
    expected = {
        "intent", "clarify", "plan", "step_start", "step_result", "route", "risk",
        "taint_flag", "policy_decision", "confirm_request", "executed", "rejected",
        "message", "ledger_append", "grounding", "error",
    }  # fmt: skip
    assert {t.value for t in AgentEventType} == expected
    AgentEvent(type=AgentEventType.MESSAGE, trace_id="t", seq=0, ts=now, data={"k": 1})


def test_numeric_fact_as_decimal() -> None:
    from decimal import Decimal

    assert NumericFact(
        label="a", kind=c.FactKind.MONEY, amount_minor=7, currency=CurrencyCode.AUD
    ).as_decimal() == Decimal(7)
    assert NumericFact(label="r", kind=c.FactKind.RATE, rate="2").as_decimal() == Decimal("2")  # type: ignore[arg-type]
    assert NumericFact(label="n", kind=c.FactKind.COUNT, count=3).as_decimal() == Decimal(3)
    assert NumericFact(label="d", kind=c.FactKind.DATE, on=date(2026, 1, 1)).as_decimal() is None


def test_route_validators() -> None:
    r = make_route()
    base = r.model_dump()
    for patch in (
        {"total_cost": make_money(-1)},
        {"total_cost": make_money(1, CurrencyCode.NPR)},
        {"delivered": make_money(8_500_000, CurrencyCode.USD)},
    ):
        with pytest.raises(ValidationError):
            c.RouteRecommendation(**{**base, **patch})
    leg2 = r.legs[0].model_copy(update={"send_amount": make_money(1, CurrencyCode.USD)})
    with pytest.raises(ValidationError):
        c.RouteRecommendation(**{**base, "legs": (r.legs[0], leg2)})


def test_derive_and_misc() -> None:
    d = c.derive(5, Tainted[int](value=1, provenance=Provenance.SYSTEM), Provenance.UNTRUSTED)
    assert d.provenance is Provenance.UNTRUSTED and d.value == 5


def test_reader_and_llm_validators() -> None:
    from corridor.contracts import PlannerStepDraft

    with pytest.raises(ValidationError):
        PlannerStepDraft(id="s", kind=StepKind.READ, tool="t", args={"source_ref": "x"})
    with pytest.raises(ValidationError):
        ReaderOutput.model_validate({"doc_type": "statement", "invoice": {"issuer": "U"}})


def _spec_payee(acct: str = "123456") -> c.NewPayeeSpec:
    t = lambda v: Tainted[str](value=v, provenance=Provenance.UNTRUSTED)  # noqa: E731
    return c.NewPayeeSpec(
        display_name=t("Ama"), region=t("NP"), currency=CurrencyCode.NPR, account_no=t(acct)
    )


def test_action_specific_fields_required_and_hashed() -> None:
    A = c.ActionType
    with pytest.raises(ValidationError):
        make_proposal(action=A.ADD_PAYEE, amount=None, payee_id=None, route=None)
    p1 = make_proposal(
        action=A.ADD_PAYEE, amount=None, payee_id=None, route=None, new_payee=_spec_payee()
    )
    p2 = make_proposal(
        action=A.ADD_PAYEE, amount=None, payee_id=None, route=None, new_payee=_spec_payee("999999")
    )
    assert p1.canonical_hash() != p2.canonical_hash()

    sched = c.ScheduleSpec(
        rrule=Tainted[str](value="FREQ=MONTHLY", provenance=Provenance.USER_TYPED),
        first_run=Tainted[date](value=date(2026, 11, 1), provenance=Provenance.USER_TYPED),
    )
    s2 = c.ScheduleSpec(
        rrule=Tainted[str](value="FREQ=WEEKLY", provenance=Provenance.USER_TYPED),
        first_run=sched.first_run,
    )
    a = make_proposal(action=A.SCHEDULE, schedule=sched)
    b = make_proposal(action=A.SCHEDULE, schedule=s2)
    assert a.canonical_hash() != b.canonical_hash()
    with pytest.raises(ValidationError):
        make_proposal(action=A.SCHEDULE)

    k1 = make_proposal(
        action=A.CONVERT, payee_id=None, route=None, target_currency=CurrencyCode.NPR
    )
    k2 = make_proposal(
        action=A.CONVERT, payee_id=None, route=None, target_currency=CurrencyCode.USD
    )
    assert k1.canonical_hash() != k2.canonical_hash()
    with pytest.raises(ValidationError):
        make_proposal(action=A.CONVERT, payee_id=None, route=None, target_currency=CurrencyCode.AUD)

    b1 = make_proposal(
        action=A.SET_BUDGET, amount=None, payee_id=None, route=None,
        budget=c.BudgetSpec(category="food", limit=make_money(5000)),
    )  # fmt: skip
    b2 = make_proposal(
        action=A.SET_BUDGET, amount=None, payee_id=None, route=None,
        budget=c.BudgetSpec(category="food", limit=make_money(5001)),
    )  # fmt: skip
    assert b1.canonical_hash() != b2.canonical_hash()

    d1 = make_proposal(
        action=A.CANCEL_SCHEDULE, amount=None, payee_id=None, route=None, schedule_id="s1"
    )
    d2 = make_proposal(
        action=A.CANCEL_SCHEDULE, amount=None, payee_id=None, route=None, schedule_id="s2"
    )
    assert d1.canonical_hash() != d2.canonical_hash()


def test_amount_must_match_route() -> None:
    with pytest.raises(ValidationError, match="exactly"):
        make_proposal(
            amount=Tainted[Money](value=make_money(99_999), provenance=Provenance.USER_TYPED)
        )
    with pytest.raises(ValidationError, match="exactly"):
        make_proposal(
            amount=Tainted[Money](
                value=make_money(100_000, CurrencyCode.USD), provenance=Provenance.USER_TYPED
            )
        )


def test_route_aggregates_validated() -> None:
    base = make_route().model_dump()
    for patch in (
        {"total_cost": make_money(501)},
        {"eta_hours": 9},
        {"savings_vs_default": make_money(1, CurrencyCode.USD)},
    ):
        with pytest.raises(ValidationError):
            c.RouteRecommendation(**{**base, **patch})


def test_invoice_draft_cannot_restate_amount_or_payee() -> None:
    A = c.ActionType
    ProposalDraft(action=A.PAY_INVOICE, source_account_ref="a", invoice_ref="inv1")
    with pytest.raises(ValidationError):
        ProposalDraft(
            action=A.PAY_INVOICE, source_account_ref="a", invoice_ref="inv1",
            amount_minor=5, currency=CurrencyCode.AUD,
        )  # fmt: skip
    with pytest.raises(ValidationError):
        ProposalDraft(action=A.PAY_INVOICE, source_account_ref="a", invoice_ref="i", payee_ref="p")


def test_llm_amount_bounds_and_pairs() -> None:
    A = c.ActionType
    for bad in (-5, 0, 10**16):
        with pytest.raises(ValidationError):
            ProposalDraft(
                action=A.TRANSFER,
                source_account_ref="a",
                amount_minor=bad,
                currency=CurrencyCode.AUD,
            )
    with pytest.raises(ValidationError):
        ReaderOutput.model_validate(
            {"doc_type": "invoice", "invoice": {"issuer": "U", "amount_minor": 5}}
        )
    with pytest.raises(ValidationError):
        ParserOutput.model_validate(
            {
                "language": "en",
                "intent": "help",
                "confidence_bps": 10000,
                "slots": {"purpose": "x" * 121},
            }
        )
    from corridor.contracts import PlannerStepDraft

    with pytest.raises(ValidationError):
        PlannerStepDraft(id="s", kind=StepKind.READ, tool="t", args={f"k{i}": 1 for i in range(17)})


def test_ledger_ts_must_be_aware(now: datetime) -> None:
    with pytest.raises(ValidationError):
        LedgerEntry(
            seq=1, ts=datetime(2026, 1, 1), trace_id="t", actor=c.LedgerActor.SYSTEM,
            event_type=c.LedgerEventType.PROPOSAL, payload_canonical_json="{}",
            payload_hash=HASH_A, policy_version_hash=HASH_A, prev_hash=HASH_A, entry_hash=HASH_A,
        )  # fmt: skip


def test_no_floats_in_safety_path_models() -> None:
    """ADR 0003: bps ints only; floats are rejected for scores, confidences, contributions."""
    for bad in (0.5, True, "5000", -1, 10001):
        with pytest.raises(ValidationError):
            RiskAssessment(score_bps=bad, level=c.RiskLevel.LOW, model_id="m")  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            c.IntentFrame(
                language="en",
                intent="help",
                confidence_bps=bad,
                parser="local",  # type: ignore[arg-type]
            )
    with pytest.raises(ValidationError):
        c.RiskReason(feature="f", text="t", contribution_bps=1.5)  # type: ignore[arg-type]
    c.RiskReason(feature="f", text="t", contribution_bps=-250)
    a = RiskAssessment(score_bps=9000, level=c.RiskLevel.HIGH, model_id="m")
    assert c.canonical_json(a)  # ledgerable: contains no float


def test_every_model_field_is_float_free() -> None:
    import types
    import typing

    def has_float(t: object) -> bool:
        if t is float:
            return True
        return any(has_float(a) for a in typing.get_args(t))

    for model in _public_models():
        for name, f in model.model_fields.items():
            ann = f.annotation
            assert not has_float(ann), f"{model.__name__}.{name} is a float"
    assert types  # keep import used
