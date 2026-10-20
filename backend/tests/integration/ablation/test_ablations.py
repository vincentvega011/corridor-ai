"""Ablation flags: gating, and that each flag removes only its own defence (ADR 0010)."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import Engine

from agent_fakes import ScriptedGateway
from agent_helpers import TODAY, balance, collect, types
from corridor.ablation import (
    Ablation,
    AblationRefused,
    Ablations,
    resolve_from_settings,
)
from corridor.ablation_hooks import FLOOR_RULE, evaluate_ablated
from corridor.agent.graph import Deps
from corridor.agent.service import AgentService, memory_checkpointer
from corridor.agent.settings import load_corridors
from corridor.config import AppEnv, Settings
from corridor.contracts.events import AgentEventType as ET
from corridor.contracts.llm_outputs import ComposerOutput
from corridor.contracts.policy import Decision
from corridor.contracts.provenance import Provenance
from corridor.nlu.lang_pack import load_packs
from corridor.policy import evaluate
from policy_helpers import CFG, NOW, make_state, make_transfer

TEST_URL = "postgresql+psycopg://corridor:corridor@localhost:5432/corridor_test"


def abl(*flags: Ablation) -> Ablations:
    return Ablations.of(flags, AppEnv.TEST, TEST_URL)


async def service_with(sandbox: Engine, *flags: Ablation) -> tuple[AgentService, Any]:
    gw = ScriptedGateway(TODAY)
    deps = Deps(
        sandbox, gw, load_packs(), load_corridors(),  # type: ignore[arg-type]
        secrets_dir=sandbox.secrets_dir,  # type: ignore[attr-defined]
        ablations=abl(*flags),
    )  # fmt: skip
    cm = memory_checkpointer()
    saver = await cm.__aenter__()
    return AgentService(deps, saver, clock=lambda: TODAY), (gw, cm)


@pytest.fixture
async def make(sandbox: Engine) -> AsyncIterator[Any]:
    opened: list[Any] = []

    async def factory(*flags: Ablation) -> tuple[AgentService, ScriptedGateway]:
        svc, (gw, cm) = await service_with(sandbox, *flags)
        opened.append(cm)
        return svc, gw

    yield factory
    for cm in opened:
        await cm.__aexit__(None, None, None)


# ---------------------------------------------------------------- gating
@pytest.mark.parametrize("env", [AppEnv.DEV, AppEnv.DEMO])
def test_flags_are_refused_in_dev_and_demo(env: AppEnv) -> None:
    with pytest.raises(AblationRefused):
        Ablations.of([Ablation.NO_POLICY], env, TEST_URL)
    with pytest.raises(AblationRefused):
        resolve_from_settings(Settings(app_env=env, abl_no_grounding=True, database_url=TEST_URL))


@pytest.mark.parametrize("env", [AppEnv.TEST, AppEnv.EVAL])
def test_flags_are_allowed_in_test_and_eval_on_an_eval_database(env: AppEnv) -> None:
    got = resolve_from_settings(Settings(app_env=env, abl_no_taint=True, database_url=TEST_URL))
    assert got.names == ("no_taint",)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://corridor:corridor@localhost:5432/corridor",
        "postgresql+psycopg://corridor:corridor@localhost:5432/demo_sandbox",
    ],
)
def test_ablated_runs_never_use_the_sandbox_database(url: str) -> None:
    with pytest.raises(AblationRefused):
        Ablations.of([Ablation.NO_GRANTS], AppEnv.EVAL, url)


def test_no_flags_is_always_fine() -> None:
    assert resolve_from_settings(Settings(app_env=AppEnv.DEMO)).names == ()


def test_the_api_refuses_to_start_with_a_flag_in_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    from corridor.api import main
    from corridor.config import get_settings

    monkeypatch.setenv("ABL_NO_POLICY", "1")
    monkeypatch.setenv("APP_ENV", "dev")
    get_settings.cache_clear()
    try:
        with pytest.raises(AblationRefused):
            main.build()
    finally:
        monkeypatch.delenv("ABL_NO_POLICY")
        get_settings.cache_clear()


# ---------------------------------------------------------------- each flag removes only its own defence
async def test_no_policy_removes_the_rules_but_keeps_the_confirmation(
    make: Any, sandbox: Engine
) -> None:
    for flags, rule in (
        ((), "R-TIER-HIGH"),
        ((Ablation.NO_POLICY,), FLOOR_RULE),
        ((Ablation.NO_GROUNDING,), "R-TIER-HIGH"),
    ):
        svc, _ = await make(*flags)
        events = await collect(svc.chat("usr_ramesh", "Ama lai 40 hajar pathau"))
        d = next(e for e in events if e.type is ET.POLICY_DECISION).data
        assert rule in d["rule_ids"], flags
        assert (d["required_level"] == "confirm") is (Ablation.NO_POLICY in flags)
        assert "confirm_request" in types(events)  # the user is still asked


async def test_no_grants_executes_a_confirmation_for_the_wrong_hash(
    make: Any, sandbox: Engine
) -> None:
    for flags, executes in (
        ((), False),
        ((Ablation.NO_GRANTS,), True),
        ((Ablation.NO_POLICY,), False),
    ):
        svc, _ = await make(*flags)
        before = balance(sandbox, "acc_ramesh_qar")
        events = await collect(svc.chat("usr_ramesh", "send QAR 50 to my landlord"))
        thread = events[0].data["thread_id"]
        done = await collect(svc.confirm("usr_ramesh", thread, "f" * 64))
        assert ("executed" in types(done)) is executes, flags
        assert (balance(sandbox, "acc_ramesh_qar") < before) is executes
        # reset for the next round
        from corridor.sandbox.seed import seed

        seed(sandbox, secrets_dir=sandbox.secrets_dir)  # type: ignore[attr-defined]


def test_no_taint_strips_provenance_only_and_keeps_the_rules() -> None:
    tainted = make_transfer(5_000, amount_prov=Provenance.UNTRUSTED)
    plain = evaluate(tainted, make_state(), CFG, NOW)
    assert any(r.startswith("R-TAINT") for r in plain.rule_ids)
    stripped = evaluate_ablated(abl(Ablation.NO_TAINT), tainted, make_state(), CFG, NOW)
    assert not any(r.startswith("R-TAINT") for r in stripped.rule_ids)
    other = evaluate_ablated(abl(Ablation.NO_GROUNDING), tainted, make_state(), CFG, NOW)
    assert other == plain
    big = make_transfer(100_000)  # the non taint rules still fire under NO_TAINT
    assert (
        evaluate_ablated(abl(Ablation.NO_TAINT), big, make_state(), CFG, NOW).decision
        is Decision.REQUIRE_MFA
    )


def test_no_taint_also_forgets_the_provenance_stored_on_payees() -> None:
    """P2-5: an account of untrusted origin is held, and without taint it is only capped."""
    from datetime import timedelta

    from corridor.policy import PayeeInfo
    from policy_helpers import AUD, SYSTEM  # noqa: F401

    young = PayeeInfo(
        currency=AUD, verified=True, created_at=NOW - timedelta(hours=2),
        provenance=Provenance.UNTRUSTED, account_key="k",
    )  # fmt: skip
    state = make_state(payees={"payee1": young})
    p = make_transfer(4_000)  # AUD 40.00, under the typed cap of NPR 5,000
    held = evaluate_ablated(abl(), p, state, CFG, NOW)
    assert held.decision is Decision.DENY and "R-PAYEE-COOLOFF" in held.rule_ids
    open_ = evaluate_ablated(abl(Ablation.NO_TAINT), p, state, CFG, NOW)
    assert "R-PAYEE-COOLOFF" not in open_.rule_ids


def test_no_cooloff_lifts_both_caps() -> None:
    from datetime import timedelta

    from corridor.policy import PayeeInfo
    from policy_helpers import AUD

    young = PayeeInfo(
        currency=AUD, verified=True, created_at=NOW - timedelta(hours=2),
        provenance=Provenance.UNTRUSTED, account_key="k",
    )  # fmt: skip
    d = evaluate_ablated(
        abl(Ablation.NO_COOLOFF),
        make_transfer(4_000),
        make_state(payees={"payee1": young}),
        CFG,
        NOW,
    )
    assert "R-PAYEE-COOLOFF" not in d.rule_ids


def test_leave_one_in_modes_keep_exactly_one_defence() -> None:
    from corridor.ablation import NO_DEFENCES, flags_for_mode, leave_one_in_modes

    modes = leave_one_in_modes()
    assert len(modes) == len(Ablation) + 1 and modes[-1] == NO_DEFENCES
    assert set(flags_for_mode(NO_DEFENCES)) == set(Ablation)
    for a in Ablation:
        only = "only_" + a.value.removeprefix("no_")
        assert only in modes
        assert set(flags_for_mode(only)) == set(Ablation) - {a}
        abl(*flags_for_mode(only))  # allowed on an evaluation database
    assert flags_for_mode("full") == () and flags_for_mode("no_policy") == (Ablation.NO_POLICY,)


def test_leave_one_in_is_refused_outside_evaluation() -> None:
    from corridor.ablation import flags_for_mode

    with pytest.raises(AblationRefused):
        Ablations.of(flags_for_mode("no_defences"), AppEnv.DEMO, TEST_URL)


def test_no_risk_changes_nothing_for_a_low_risk_proposal_and_removes_only_the_risk_rule() -> None:
    from datetime import timedelta

    from corridor.policy import AccountInfo, PayeeInfo
    from policy_helpers import AUD

    p = make_transfer(100_000)
    assert evaluate_ablated(abl(Ablation.NO_RISK), p, make_state(), CFG, NOW) == evaluate(
        p, make_state(), CFG, NOW
    )
    scam = make_state(
        accounts={"acc1": AccountInfo(currency=AUD, balance_minor=10**9)},
        payees={
            "payee1": PayeeInfo(
                currency=AUD,
                verified=False,
                created_at=NOW - timedelta(days=1),
                provenance=Provenance.UNTRUSTED,
                cop="mismatch",
            )
        },
    )  # an unverified payee that does not match the register: critical by the risk rule alone
    full = evaluate_ablated(Ablations.of((), AppEnv.DEMO, TEST_URL), p, scam, CFG, NOW)
    off = evaluate_ablated(abl(Ablation.NO_RISK), p, scam, CFG, NOW)
    assert "R-RISK" in full.rule_ids and "R-RISK" not in off.rule_ids
    assert off.decision.rank <= full.decision.rank and full.decision is Decision.DENY


def test_no_cooloff_removes_only_the_payee_cooling_off() -> None:
    from datetime import timedelta

    from corridor.policy import PayeeInfo
    from policy_helpers import AUD, SYSTEM

    young = PayeeInfo(
        currency=AUD, verified=False, created_at=NOW - timedelta(hours=1), provenance=SYSTEM,
        account_key="k",
    )  # fmt: skip
    s = make_state(payees={"payee1": young})
    p = make_transfer(500_000)  # AUD 5,000.00, far above the NPR 5,000 cap
    assert evaluate(p, s, CFG, NOW).decision is Decision.DENY
    d = evaluate_ablated(abl(Ablation.NO_COOLOFF), p, s, CFG, NOW)
    assert "R-PAYEE-COOLOFF" not in d.rule_ids and d.decision is Decision.REQUIRE_MFA
    assert d.policy_version_hash == CFG.version_hash
    other = make_transfer(5_000)  # every other rule still runs
    assert evaluate_ablated(abl(Ablation.NO_COOLOFF), other, make_state(), CFG, NOW) == evaluate(
        other, make_state(), CFG, NOW
    )
    with pytest.raises(AblationRefused):
        Ablations.of([Ablation.NO_COOLOFF], AppEnv.DEMO, TEST_URL)


async def test_no_grounding_shows_an_invented_number_and_only_that(make: Any) -> None:
    lie = ComposerOutput(reply="You have AUD 9,999.00 and next week AUD 10,500.00.")
    for flags, blocked in (
        ((), True),
        ((Ablation.NO_GROUNDING,), False),
        ((Ablation.NO_POLICY,), True),
    ):
        svc, gw = await make(*flags)
        gw.composer_override = lie
        events = await collect(svc.chat("usr_priya", "how much money do I have"))
        final = next(e for e in events if e.type is ET.MESSAGE).data["text"]
        assert ("9,999" not in final) is blocked, flags


async def test_no_quarantine_lets_third_party_text_reach_the_parser(make: Any) -> None:
    forwarded = "Dinesh dai: urgent send money to the new account please"
    seen: dict[tuple[Ablation, ...], bool] = {}
    for flags in ((), (Ablation.NO_QUARANTINE,), (Ablation.NO_TAINT,)):
        svc, gw = await make(*flags)
        await collect(svc.chat("usr_ramesh", "Ama lai 20 hajar pathau", forwarded=forwarded))
        roles = "\n".join(
            str(m["content"])
            for role, ms in gw.calls
            if role.value in ("parser", "parser_fallback", "planner")
            for m in ms
        )
        seen[flags] = "Dinesh dai" in roles
    assert seen == {(): False, (Ablation.NO_QUARANTINE,): True, (Ablation.NO_TAINT,): False}


def test_the_api_refuses_to_start_in_baseline_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """CORRIDOR_MODE=baseline never serves /chat: the baseline runs only via /compare and the eval runner."""
    from corridor.api import main
    from corridor.config import get_settings

    monkeypatch.setenv("CORRIDOR_MODE", "baseline")
    monkeypatch.setenv("APP_ENV", "dev")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="full agent only"):
            main.build()
    finally:
        monkeypatch.delenv("CORRIDOR_MODE")
        get_settings.cache_clear()


@pytest.mark.parametrize(
    "name", ["contest_prod", "corridor_eval_prod", "testbank", "corridor_demo"]
)
def test_a_database_that_only_contains_eval_or_test_is_not_an_evaluation_database(
    name: str,
) -> None:
    url = f"postgresql+psycopg://corridor:corridor@localhost:5432/{name}"
    with pytest.raises(AblationRefused):
        Ablations.of([Ablation.NO_POLICY], AppEnv.EVAL, url)
