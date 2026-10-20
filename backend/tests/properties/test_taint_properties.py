"""Provenance can never increase through computation (docs/spec/modules.md 5.6, INV-4)."""

from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from corridor.contracts import Provenance, Tainted
from corridor.contracts.provenance import trust_rank
from corridor.taint import derive, derive_from_model, is_tainted, lowest_trust

PROVS = st.sampled_from(list(Provenance))
tainted_ints = st.builds(
    lambda v, p: Tainted[Any](value=v, provenance=p), st.integers(-(10**6), 10**6), PROVS
)


@given(st.lists(tainted_ints, min_size=1, max_size=5))
def test_arithmetic_takes_the_lowest_trust_and_never_confirms(inputs: list[Tainted[Any]]) -> None:
    out = derive(sum(t.value for t in inputs), *inputs)
    assert all(trust_rank(out.provenance) <= trust_rank(t.provenance) for t in inputs)
    assert out.provenance is not Provenance.USER_CONFIRMED


@given(st.lists(tainted_ints, min_size=1, max_size=4))
def test_string_formatting_cannot_raise_trust(inputs: list[Tainted[Any]]) -> None:
    text = derive(" ".join(f"{t.value:+d}" for t in inputs), *inputs)
    assert trust_rank(text.provenance) == min(trust_rank(t.provenance) for t in inputs) or (
        text.provenance is Provenance.USER_TYPED
    )


@given(tainted_ints)
def test_serialisation_round_trips_keep_the_provenance(t: Tainted[Any]) -> None:
    again = Tainted[Any].model_validate_json(t.model_dump_json())
    assert again.provenance is t.provenance
    assert Tainted[Any].model_validate(t.model_dump(mode="json")).provenance is t.provenance


@given(st.lists(tainted_ints, max_size=4))
def test_a_model_call_never_yields_more_than_model_inferred(inputs: list[Tainted[Any]]) -> None:
    out = derive_from_model("generated text", *inputs)
    assert trust_rank(out.provenance) <= trust_rank(Provenance.MODEL_INFERRED)


@given(PROVS)
def test_confirmation_never_propagates_to_a_derived_value(p: Provenance) -> None:
    assert lowest_trust(p, Provenance.USER_CONFIRMED) is not Provenance.USER_CONFIRMED
    assert lowest_trust(Provenance.USER_CONFIRMED) is Provenance.USER_TYPED


def test_is_tainted() -> None:
    assert is_tainted(Provenance.UNTRUSTED) and is_tainted(Provenance.MODEL_INFERRED)
    assert not any(
        is_tainted(p) for p in (Provenance.USER_TYPED, Provenance.USER_CONFIRMED, Provenance.SYSTEM)
    )
