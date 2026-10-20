"""Hypothesis property tests for canonical JSON, hashing, provenance and decisions."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from corridor.contracts import (
    ActionProposal,
    ActionType,
    CurrencyCode,
    Decision,
    Money,
    Provenance,
    RouteLeg,
    RouteRecommendation,
    Tainted,
    canonical_json,
    combine,
    lowest_trust,
    sha256_hex,
    trust_rank,
)

json_leaf = st.none() | st.booleans() | st.integers() | st.text(max_size=12)
json_value = st.recursive(
    json_leaf,
    lambda c: st.lists(c, max_size=4) | st.dictionaries(st.text(max_size=6), c, max_size=4),
    max_leaves=12,
)


@given(json_value)
def test_canonical_json_roundtrip_stable(obj: Any) -> None:
    text = canonical_json(obj)
    assert json.loads(text) == obj
    assert canonical_json(json.loads(text)) == text


@given(st.dictionaries(st.text(max_size=6), st.integers(), min_size=2, max_size=8), st.randoms())
def test_canonical_json_key_order_independent(d: dict[str, int], rnd: Any) -> None:
    items = list(d.items())
    rnd.shuffle(items)
    assert canonical_json(dict(items)) == canonical_json(d)
    assert sha256_hex(dict(items)) == sha256_hex(d)


def test_canonical_format_details() -> None:
    assert canonical_json({"b": 1, "a": [True, None]}) == '{"a":[true,null],"b":1}'
    assert canonical_json(Decimal("1.500")) == '"1.5"'
    assert canonical_json(Decimal("1E+2")) == '"100"'
    assert canonical_json(Decimal("-0.0")) == '"0"'
    assert canonical_json(CurrencyCode.NPR) == '"NPR"'
    assert canonical_json(datetime(2026, 10, 10, 5, 30, tzinfo=UTC)) == '"2026-10-10T05:30:00Z"'
    assert canonical_json("नेपाल") == '"नेपाल"'


def test_canonical_decimal_never_rounds() -> None:
    a = canonical_json(Decimal("1234567890123456789012.12345678"))
    b = canonical_json(Decimal("1234567890123456789012.12345679"))
    assert a != b and a == '"1234567890123456789012.12345678"'


def test_canonical_rejects_floats_naive_and_sets() -> None:
    for bad in (1.5, {1, 2}, b"x", datetime(2026, 1, 1)):
        with pytest.raises((TypeError, ValueError)):
            canonical_json(bad)
    with pytest.raises(TypeError):
        canonical_json({1: "a"})


# --- provenance ------------------------------------------------------------------------
provs = st.sampled_from(list(Provenance))


@given(st.lists(provs, min_size=1, max_size=6))
def test_lowest_trust_never_higher_than_any_input(ps: list[Provenance]) -> None:
    out = lowest_trust(*ps)
    assert all(trust_rank(out) <= trust_rank(p) for p in ps)
    assert out is not Provenance.USER_CONFIRMED
    assert out in ps or (out is Provenance.USER_TYPED and Provenance.USER_CONFIRMED in ps)


@given(st.lists(provs, min_size=1, max_size=6), st.lists(provs, max_size=4))
def test_adding_inputs_never_raises_trust(ps: list[Provenance], more: list[Provenance]) -> None:
    assert trust_rank(lowest_trust(*ps, *more)) <= trust_rank(lowest_trust(*ps))


def test_lowest_trust_ordering_and_empty() -> None:
    P = Provenance
    assert lowest_trust(P.SYSTEM, P.UNTRUSTED) is P.UNTRUSTED
    assert lowest_trust(P.SYSTEM, P.MODEL_INFERRED, P.USER_TYPED) is P.MODEL_INFERRED
    assert lowest_trust(P.USER_CONFIRMED) is P.USER_TYPED
    with pytest.raises(ValueError):
        lowest_trust()


def test_no_upgrade_api_exists() -> None:
    names = {n for n in dir(Tainted) if not n.startswith("_")}
    assert not {n for n in names if "upgrade" in n or "confirm" in n or "trust" in n}


@given(provs, st.one_of(st.integers(), st.text(max_size=8)), st.none() | st.text(max_size=5))
def test_tainted_json_roundtrip_keeps_provenance(p: Provenance, v: Any, ref: str | None) -> None:
    t = Tainted[Any](value=v, provenance=p, source_ref=ref)
    back = Tainted[Any].model_validate_json(t.model_dump_json())
    assert back.provenance is p and back.value == v and back.source_ref == ref
    assert Tainted[Any].model_validate(t.model_dump()).provenance is p


# --- proposal hash ---------------------------------------------------------------------
def _route(
    fee: int, rate: str, provider: str, eta: int, minor: int, cur: CurrencyCode
) -> RouteRecommendation:
    send = Money(amount_minor=minor, currency=cur)
    got = Money(amount_minor=8_000_000, currency=CurrencyCode.NPR)
    leg = RouteLeg(
        provider_id=provider,
        send_amount=send,
        fee=Money(amount_minor=fee, currency=cur),
        effective_rate=rate,
        delivered=got,
        eta_hours=eta,
    )
    return RouteRecommendation(
        legs=(leg,),
        total_cost=Money(amount_minor=fee, currency=cur),
        effective_rate=rate,
        delivered=got,
        eta_hours=eta,
        savings_vs_default=Money(amount_minor=0, currency=cur),
    )


ids = st.text(alphabet="abcdef0123456789", min_size=1, max_size=8)


def _proposal(
    action: ActionType, user: str, acc: str, payee: str, minor: int, cur: CurrencyCode,
    idem: str, fee: int, rate: str, provider: str, eta: int, prov: Provenance,
) -> ActionProposal:  # fmt: skip
    return ActionProposal(
        proposal_id="pid",
        action=action,
        user_id=user,
        source_account_id=Tainted[str](value=acc, provenance=prov),
        payee_id=Tainted[str](value=payee, provenance=prov),
        amount=Tainted[Money](value=Money(amount_minor=minor, currency=cur), provenance=prov),
        invoice_id="inv",
        route=_route(fee, rate, provider, eta, minor, cur),
        idempotency_key=idem,
    )


base_args = st.tuples(
    st.sampled_from([ActionType.TRANSFER, ActionType.PAY_INVOICE]),
    ids, ids, ids,
    st.integers(min_value=1, max_value=10**12),
    st.sampled_from(list(CurrencyCode)),
    ids,
    st.integers(min_value=0, max_value=10**6),
    st.sampled_from(["85.1", "85.2", "0.00000001"]),
    ids,
    st.integers(min_value=0, max_value=100),
    provs,
)  # fmt: skip


def _mutate(args: tuple[Any, ...], idx: int) -> tuple[Any, ...]:
    a = list(args)
    v = a[idx]
    if idx == 0:
        a[0] = ActionType.PAY_INVOICE if v is ActionType.TRANSFER else ActionType.TRANSFER
    elif idx == 5:
        a[5] = next(c for c in CurrencyCode if c is not v)
    elif idx == 8:
        a[8] = "99.9" if v != "99.9" else "98.9"
    elif isinstance(v, str):
        a[idx] = v + "x"
    else:
        a[idx] = v + 1
    return tuple(a)


@settings(max_examples=60)
@given(base_args, st.sampled_from([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]))
def test_hash_changes_when_any_hashed_field_changes(args: tuple[Any, ...], idx: int) -> None:
    # args index: 0 action,1 user,2 acc,3 payee,4 minor,5 cur,6 idem,7 fee,8 rate,9 provider,10 eta
    assert _proposal(*args).canonical_hash() != _proposal(*_mutate(args, idx)).canonical_hash()


@settings(max_examples=60)
@given(base_args, provs, st.text(max_size=30))
def test_hash_unchanged_by_non_hashed_fields(
    args: tuple[Any, ...], prov: Provenance, text: str
) -> None:
    from corridor.contracts import RiskAssessment, RiskLevel, RiskReason

    p = _proposal(*args)
    q = p.model_copy(
        update={
            "proposal_id": "different",
            "derived_from_docs": ("b" * 64,),
            "risk": RiskAssessment(
                score_bps=9000,
                level=RiskLevel.HIGH,
                top_reasons=(RiskReason(feature="f", text=text[:120]),),
                model_id="m",
            ),
            "payee_id": Tainted[str](value=p.payee_id.value, provenance=prov),  # type: ignore[union-attr]
        }
    )
    assert p.canonical_hash() == q.canonical_hash()


@given(base_args)
def test_hash_deterministic_and_roundtrip(args: tuple[Any, ...]) -> None:
    p = _proposal(*args)
    assert p.canonical_hash() == _proposal(*args).canonical_hash()
    assert (
        ActionProposal.model_validate_json(p.model_dump_json()).canonical_hash()
        == p.canonical_hash()
    )


# --- decision combination --------------------------------------------------------------
decisions = st.sampled_from(list(Decision))


@given(decisions, decisions, decisions)
def test_combine_associative_commutative(a: Decision, b: Decision, c: Decision) -> None:
    assert combine([combine([a, b]), c]) == combine([a, combine([b, c])]) == combine([a, b, c])
    assert combine([a, b]) == combine([b, a])
    assert combine([a, a]) == a


@given(st.lists(decisions, min_size=1, max_size=8))
def test_combine_is_most_restrictive(ds: list[Decision]) -> None:
    out = combine(ds)
    assert out in ds
    assert all(d.rank <= out.rank for d in ds)
    # adding any decision can never make the result less restrictive
    for extra in Decision:
        assert combine([*ds, extra]).rank >= out.rank
