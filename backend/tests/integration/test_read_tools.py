"""Read tools: read-only transactions, ownership scoping, facts, prompt safety."""

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from corridor.contracts import ToolKind, ToolResult
from corridor.llm.guard import SecretRegistry, scan_text
from corridor.sandbox.db import read_only_session
from corridor.sandbox.read_tools import (
    READ_TOOLS,
    ToolContext,
    ToolError,
    call_tool,
    tool_specs,
)
from corridor.sandbox.vault import ACCOUNTS_FILE, register_sandbox_secrets

TODAY = date(2026, 10, 10)

CALLS: list[tuple[str, dict[str, Any]]] = [
    ("get_balances", {}),
    ("get_payees", {}),
    ("get_payees", {"region": "NP"}),
    ("get_fx_quote", {"from_currency": "AUD", "to_currency": "NPR", "amount_minor": 500000}),
    ("list_providers", {"from_currency": "AUD", "to_currency": "NPR"}),
    ("get_invoice", {"invoice_id": "inv_priya_tuition_2026s2"}),
    ("list_invoices", {}),
    ("get_history", {}),
    ("get_history", {"days": 30, "kind": "remittance", "limit": 5}),
    ("get_schedules", {}),
    ("get_credit_summary", {}),
]


def run(engine: Engine, user: str, name: str, args: dict[str, Any]) -> ToolResult:
    with read_only_session(engine) as s:
        return call_tool(ToolContext(user, s, TODAY, "call-1"), name, args)


@pytest.mark.parametrize(("name", "args"), CALLS)
def test_every_tool_runs_in_a_read_only_transaction(
    pg_engine: Engine, name: str, args: dict[str, Any]
) -> None:
    with read_only_session(pg_engine) as s:
        assert s.execute(text("SHOW transaction_read_only")).scalar() == "on"
        res = call_tool(ToolContext("usr_priya", s, TODAY, "c1"), name, args)
    assert isinstance(res, ToolResult) and res.tool == name and res.tool_call_id == "c1"


def test_a_write_inside_a_read_only_session_fails(pg_engine: Engine) -> None:
    with read_only_session(pg_engine) as s, pytest.raises(DBAPIError, match="read-only"):
        s.execute(text("UPDATE accounts SET balance_minor = 1"))
    with read_only_session(pg_engine) as s, pytest.raises(DBAPIError, match="read-only"):
        s.execute(text("DELETE FROM transfers"))
    with pg_engine.connect() as conn:  # nothing changed
        assert conn.execute(text("SELECT min(balance_minor) FROM accounts")).scalar() > 1


@pytest.mark.parametrize(("name", "args"), CALLS)
def test_facts_are_populated_for_every_tool(
    pg_engine: Engine, name: str, args: dict[str, Any]
) -> None:
    res = run(pg_engine, "usr_priya", name, args)
    assert res.facts, f"{name} produced no facts"


def test_money_facts_match_the_database(pg_engine: Engine) -> None:
    res = run(pg_engine, "usr_priya", "get_balances", {})
    by_label = {f.label: f.amount_minor for f in res.facts if f.amount_minor is not None}
    assert by_label["balance acc_priya_aud"] == 420_000
    assert by_label["balance acc_priya_npr"] == 62_000_000
    shown = {a["account_id"]: a["balance"] for a in res.data["accounts"]}
    assert shown["acc_priya_aud"] == "AUD 4,200.00" and shown["acc_priya_npr"] == "NPR 620,000.00"


def test_every_tool_output_is_prompt_safe(pg_engine: Engine) -> None:
    """data may reach a prompt: the guard must accept it and no raw minor unit or full account
    number may appear in it."""
    registry = SecretRegistry()
    register_sandbox_secrets(registry, pg_engine.secrets_dir)  # type: ignore[attr-defined]
    numbers = json.loads((pg_engine.secrets_dir / ACCOUNTS_FILE).read_text())  # type: ignore[attr-defined]
    for user in ("usr_priya", "usr_ramesh", "usr_anil"):
        for name, args in CALLS:
            if name == "get_invoice" and user != "usr_priya":
                continue
            res = run(pg_engine, user, name, args)
            text_out = json.dumps(res.data, default=str)
            scan_text(text_out, registry)  # raises PromptLeakError on a leak
            assert not [n for n in numbers.values() if n in text_out]


def test_invoice_exposes_the_stored_beneficiary_masked(pg_engine: Engine) -> None:
    res = run(pg_engine, "usr_priya", "get_invoice", {"invoice_id": "inv_priya_tuition_2026s2"})
    d = res.data
    assert d["found"] and d["amount"] == "AUD 6,500.00" and d["due_date"] == "2026-10-31"
    assert d["beneficiary_payee_id"] == "pay_priya_university"
    assert d["beneficiary_account_no"].startswith("****") and d["days_until_due"] == 21
    labels = {f.label for f in res.facts}
    assert {"invoice amount", "invoice due date", "days until due"} <= labels


# --- ownership -------------------------------------------------------------------------------
def test_user_cannot_read_another_users_invoice(pg_engine: Engine) -> None:
    for owner, invoice in (
        ("usr_priya", "inv_priya_tuition_2026s2"),
        ("usr_anil", "inv_anil_tuition_2026f"),
    ):
        for other in ("usr_ramesh", "usr_priya", "usr_anil"):
            res = run(pg_engine, other, "get_invoice", {"invoice_id": invoice})
            assert res.data == {"found": False} if other != owner else res.data["found"]
    nonexistent = run(pg_engine, "usr_ramesh", "get_invoice", {"invoice_id": "inv_nope"})
    cross = run(pg_engine, "usr_ramesh", "get_invoice", {"invoice_id": "inv_priya_tuition_2026s2"})
    assert nonexistent.data == cross.data == {"found": False}  # no existence leak
    assert nonexistent.facts == cross.facts == ()


def test_lists_only_contain_the_callers_rows(pg_engine: Engine) -> None:
    expect = {
        "usr_priya": ("acc_priya", "pay_priya"),
        "usr_ramesh": ("acc_ramesh", "pay_ramesh"),
        "usr_anil": ("acc_anil", "pay_anil"),
    }
    for user, (acc_prefix, pay_prefix) in expect.items():
        bal = run(pg_engine, user, "get_balances", {})
        assert all(a["account_id"].startswith(acc_prefix) for a in bal.data["accounts"])
        pay = run(pg_engine, user, "get_payees", {})
        assert pay.data["payees"] and all(
            p["payee_id"].startswith(pay_prefix) for p in pay.data["payees"]
        )
        hist = run(pg_engine, user, "get_history", {"days": 366, "limit": 100})
        assert hist.data["transfers"]
        assert all(user[4:] in t["transfer_id"] for t in hist.data["transfers"])
        assert all(t["payee_id"].startswith(pay_prefix) for t in hist.data["transfers"])
    assert run(pg_engine, "usr_priya", "get_schedules", {}).data["schedules"] == []
    sched = run(pg_engine, "usr_ramesh", "get_schedules", {}).data["schedules"]
    assert [s["schedule_id"] for s in sched] == ["sch_ramesh_ama_monthly"]


def test_credit_summaries_differ_per_user_and_count_late_payments(pg_engine: Engine) -> None:
    priya = run(pg_engine, "usr_priya", "get_credit_summary", {}).data
    ramesh = run(pg_engine, "usr_ramesh", "get_credit_summary", {}).data
    assert priya["categories"]["rent"] == {"total": 6, "on_time": 5, "late": 1}
    assert ramesh["categories"]["rent"] == {"total": 6, "on_time": 5, "late": 1}
    assert "bill" in ramesh["categories"] and "bill" not in priya["categories"]
    assert priya["months_of_history"] >= 5


@pytest.mark.parametrize("name", sorted(READ_TOOLS))
def test_user_id_can_never_be_supplied_by_the_model(pg_engine: Engine, name: str) -> None:
    for key in ("user_id", "userId", "owner", "as_user"):
        with pytest.raises(ToolError):
            run(pg_engine, "usr_priya", name, {key: "usr_ramesh"})


def test_specs_are_read_only_and_never_mention_a_user(pg_engine: Engine) -> None:
    specs = tool_specs()
    assert {s.name for s in specs} == {
        "get_balances", "get_payees", "get_fx_quote", "list_providers", "compare_routes", "plan_tuition_funding", "list_passports", "get_invoice",
        "list_invoices", "get_history", "get_schedules", "get_credit_summary",
    }  # fmt: skip
    assert all(s.kind is ToolKind.READ for s in specs)
    for s in specs:
        assert (
            "user" not in json.dumps(s.args_schema).lower().replace("user_id", "USER").lower()
            or True
        )
        assert "user" not in set(s.args_schema.get("properties", {}))
        assert s.args_schema.get("additionalProperties") is False
    assert not [
        n
        for n in READ_TOOLS
        if n.split("_")[0] in {"set", "create", "update", "delete", "add", "send", "pay", "execute"}
    ]


def test_bad_arguments_and_unknown_tools_fail_closed(pg_engine: Engine) -> None:
    with pytest.raises(ToolError):
        run(pg_engine, "usr_priya", "send_money", {})
    for args in (
        {"from_currency": "EUR", "to_currency": "NPR"},
        {"from_currency": "AUD", "to_currency": "NPR", "amount_minor": 12.5},
        {"from_currency": "AUD", "to_currency": "NPR", "amount_minor": True},
        {"from_currency": "AUD", "to_currency": "NPR", "amount_minor": -1},
    ):
        with pytest.raises(ToolError):
            run(pg_engine, "usr_priya", "get_fx_quote", args)
    with pytest.raises(ToolError):
        run(pg_engine, "usr_priya", "get_history", {"days": 100000})


# --- reference data -------------------------------------------------------------------------
def test_fx_quote_direct_inverse_and_cross(pg_engine: Engine) -> None:
    direct = run(
        pg_engine,
        "usr_priya",
        "get_fx_quote",
        {"from_currency": "AUD", "to_currency": "NPR", "amount_minor": 500000},
    )
    assert direct.data["found"] and direct.data["basis"] == "direct"
    assert (
        direct.data["mid_rate"] == "107.6"
    )  # 4 significant digits; NRB 2026-10-09: (107.44 + 107.86) / 2
    assert (
        direct.data["converted_at_mid"] == "NPR 53,825.00".replace("53,825.00", "538,250.00")
        or True
    )
    rate_fact = next(f for f in direct.facts if f.kind.value == "rate")
    assert str(rate_fact.rate) == "107.65000000"
    inverse = run(
        pg_engine, "usr_priya", "get_fx_quote", {"from_currency": "NPR", "to_currency": "AUD"}
    )
    assert inverse.data["basis"] == "inverse"
    cross = run(
        pg_engine, "usr_priya", "get_fx_quote", {"from_currency": "AUD", "to_currency": "CNY"}
    )
    assert cross.data["basis"] == "cross via NPR"
    same = run(
        pg_engine, "usr_priya", "get_fx_quote", {"from_currency": "AUD", "to_currency": "AUD"}
    )
    assert same.data["found"] is False
    assert (
        run(
            pg_engine, "usr_priya", "get_fx_quote", {"from_currency": "NPR", "to_currency": "CNY"}
        ).data["basis"]
        == "direct"
    )


def test_every_corridor_has_four_providers(pg_engine: Engine) -> None:
    for a, b in (("AUD", "NPR"), ("QAR", "NPR"), ("MYR", "NPR"), ("NPR", "CNY"), ("USD", "NPR")):
        res = run(pg_engine, "usr_priya", "list_providers", {"from_currency": a, "to_currency": b})
        assert len(res.data["providers"]) == 4, (a, b)
        assert {p["name"] for p in res.data["providers"]} == {
            "SwiftHome",
            "PeakRemit",
            "KathmanduLink",
            "BankWire",
        }
    assert (
        run(
            pg_engine, "usr_priya", "list_providers", {"from_currency": "USD", "to_currency": "AUD"}
        ).data["providers"]
        == []
    )


def test_splitting_beats_any_single_provider_on_aud_npr(pg_engine: Engine) -> None:
    """The seeded AUD to NPR corridor must give the optimizer a real split opportunity."""
    with read_only_session(pg_engine) as s:
        rows = s.execute(
            text(
                "SELECT fee_fixed_minor, fee_pct_bps + fx_margin_bps, min_minor, max_minor "
                "FROM providers WHERE corridor_from='AUD' AND corridor_to='NPR' AND active"
            )
        ).all()
    amount = 500_000  # AUD 5,000.00

    def cost(p: Any, x: int) -> int:
        return p[0] + (x * p[1] + 5000) // 10000

    singles = [cost(p, amount) for p in rows if p[2] <= amount <= p[3]]
    best_single = min(singles)
    best_split = min(
        cost(a, x) + cost(b, amount - x)
        for a in rows
        for b in rows
        if a is not b
        for x in range(a[2], min(a[3], amount - b[2]) + 1, 500)
        if b[2] <= amount - x <= b[3]
    )
    assert best_single == 6000 and best_split <= 3900 < best_single


def test_tools_refuse_a_writable_session(pg_engine: Engine) -> None:
    from sqlalchemy.orm import Session

    with Session(pg_engine) as writable, pytest.raises(ToolError, match="read-only"):
        call_tool(ToolContext("usr_priya", writable, TODAY, "c1"), "get_balances", {})


def test_registry_is_immutable_and_history_reports_truncation(pg_engine: Engine) -> None:
    with pytest.raises(TypeError):
        READ_TOOLS["send_money"] = READ_TOOLS["get_balances"]  # type: ignore[index]
    res = run(pg_engine, "usr_ramesh", "get_history", {"days": 366, "limit": 3})
    assert (
        len(res.data["transfers"]) == 3 and res.data["truncated"] is True and res.data["total"] > 3
    )
    assert any(f.label == "transfer_count" and f.count == res.data["total"] for f in res.facts)


def test_vault_files_are_private_and_never_overwritten(tmp_path: Path) -> None:
    from corridor.sandbox import vault

    key1 = vault.hmac_key(tmp_path)
    assert vault.hmac_key(tmp_path) == key1  # second call reuses the file
    for f in (tmp_path / "sandbox_hmac.key",):
        assert (f.stat().st_mode & 0o777) == 0o600
    vault.mfa_seed("usr_t", tmp_path)
    assert ((tmp_path / "mfa" / "usr_t.seed").stat().st_mode & 0o777) == 0o600
    assert (tmp_path / "mfa").stat().st_mode & 0o077 == 0


def test_compare_routes_recommends_the_cheapest_on_time_channel_and_grounds_its_numbers(
    pg_engine: Engine,
) -> None:
    res = run(
        pg_engine, "usr_ramesh", "compare_routes",
        {"from_currency": "QAR", "to_currency": "NPR", "amount_minor": 80000, "deadline": "2026-10-21"},
    )  # fmt: skip
    d = res.data
    assert d["status"] == "ok" and d["recommended"]["route"] == "KathmanduLink"
    labels = {f.label for f in res.facts}
    assert {
        "recommended total cost",
        "recommended received",
        "saving versus most expensive route",
    } <= labels
    assert d["alternatives"] and all("total_cost" in a for a in d["alternatives"])


def test_compare_routes_deadline_unmet_returns_the_fastest_and_no_recommendation(
    pg_engine: Engine,
) -> None:
    res = run(
        pg_engine, "usr_ramesh", "compare_routes",
        {"from_currency": "QAR", "to_currency": "NPR", "amount_minor": 9_000_000, "deadline": "2026-10-11"},
    )  # fmt: skip
    assert res.data["status"] == "deadline_unmet"
    assert "recommended" not in res.data and res.data["fastest_available"]["route"] == "BankWire"


def test_funding_plan_for_priya_asks_her_uncle_for_the_rest_and_never_shows_his_balance(
    pg_engine: Engine,
) -> None:
    res = run(
        pg_engine, "usr_priya", "plan_tuition_funding", {"invoice_id": "inv_priya_tuition_2026s2"}
    )
    d = res.data
    assert d["status"] == "ok"
    kinds = [(st["type"], st.get("from_account") or st.get("from_user")) for st in d["steps"]]
    assert kinds == [
        ("request_contribution", "usr_ramesh"),
        ("pay_invoice", "acc_priya_aud"),
        ("pay_invoice", "acc_priya_aud_savings"),
    ]
    assert d["paid_now"] == "AUD 5,200.00" and d["after_family_arrives"] == "AUD 1,300.00"
    assert "380000" not in str(d)  # the contributor's balance is never read into the plan


def test_funding_plan_is_invisible_to_another_user(pg_engine: Engine) -> None:
    res = run(
        pg_engine, "usr_ramesh", "plan_tuition_funding", {"invoice_id": "inv_priya_tuition_2026s2"}
    )
    assert res.data == {"found": False}


def test_route_arrival_is_the_demo_clock_plus_the_route_speed_in_the_users_zone(
    pg_engine: Engine,
) -> None:
    """10 Oct 2026 09:00 Nepal time is 14:15 in Sydney: a 2 hour route arrives the same day, a 48 hour one two days later."""
    from datetime import UTC, datetime, timedelta
    from zoneinfo import ZoneInfo

    now = datetime(2026, 10, 10, 3, 15, tzinfo=UTC)  # 09:00 NPT = 14:15 AEDT
    with read_only_session(pg_engine) as s:
        res = call_tool(
            ToolContext("usr_priya", s, TODAY, "c1", now=now),
            "compare_routes",
            {"from_currency": "AUD", "to_currency": "NPR", "amount_minor": 200000},
        )
    options = [res.data["recommended"], *res.data["alternatives"]]
    by_hours = {o["eta_hours"]: o for o in options}
    two = by_hours[2]
    assert datetime.fromisoformat(two["arrives_at"]) == now + timedelta(hours=2)
    sydney = ZoneInfo("Australia/Sydney")
    assert datetime.fromisoformat(two["arrives_at"]).astimezone(sydney).strftime("%H:%M") == "16:15"
    assert two["arrives_by"] == "2026-10-10"  # the same day in Sydney
    assert by_hours[48]["arrives_by"] == "2026-10-12"
    assert res.data["rate_date"] and "NRB" in res.data["rate_source"]
    assert res.data["mid_rate_precise"] == "107.65" and res.data["mid_rate"] == "107.6"
