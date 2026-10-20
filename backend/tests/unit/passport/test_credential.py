"""Credit Passport credential: signatures, selective disclosure and tampering (P3-1, ADR 0015)."""

import copy
import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from corridor.passport import credential as c

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
KEY = "ab" * 32
HOLDER = "cd" * 32
NONCE = "n" * 32
PUB = c.public_key_hex(KEY)
CLAIMS = [
    {"id": "rent_on_time", "on_time_payments": 5, "payments": 6},
    {"id": "tuition_on_time", "on_time_payments": 1, "payments": 1},
    {"id": "no_failed_payments", "failed_payments": 0, "months": 6},
]


def issued() -> c.Issued:
    return c.issue(
        CLAIMS, holder_seed="usr_priya:pp_1", key_hex=KEY, holder_key_hex=HOLDER, now=NOW
    )


def test_a_presentation_with_one_claim_verifies_and_shows_only_that_claim() -> None:
    iss = issued()
    pres = c.present(
        iss.token,
        list(iss.disclosures),
        ["rent_on_time"],
        audience="a new landlord",
        holder_key_hex=HOLDER,
        now=NOW,
        nonce=NONCE,
    )
    v = c.verify(pres, trusted_public_key_hex=PUB, now=NOW, expected_audience="a new landlord")
    assert v.valid and [x["id"] for x in v.claims] == ["rent_on_time"]
    assert v.audience == "a new landlord" and v.holder == iss.holder
    blob = str(pres)
    assert "tuition" not in blob and "failed_payments" not in blob  # nothing else leaks


def test_the_credential_never_carries_the_user_id_or_claim_values() -> None:
    iss = issued()
    body = c.b64d(iss.token.split(".")[0]).decode()
    assert "usr_priya" not in body and "on_time_payments" not in body and "rent" not in body


def test_a_tampered_disclosed_claim_fails() -> None:
    iss = issued()
    pres = c.present(
        iss.token,
        list(iss.disclosures),
        ["rent_on_time"],
        audience="x",
        holder_key_hex=HOLDER,
        now=NOW,
        nonce=NONCE,
    )
    pres["disclosures"][0]["claim"]["on_time_payments"] = 6
    assert (
        c.verify(pres, trusted_public_key_hex=PUB, now=NOW, expected_audience="x").reason
        == "claim_not_signed"
    )


def test_a_tampered_or_swapped_signature_fails() -> None:
    iss = issued()
    pres = c.present(
        iss.token,
        list(iss.disclosures),
        ["rent_on_time"],
        audience="x",
        holder_key_hex=HOLDER,
        now=NOW,
        nonce=NONCE,
    )
    body, sig = pres["credential"].split(".")
    bad = dict(pres, credential=body + "." + ("A" if sig[0] != "A" else "B") + sig[1:])
    assert (
        c.verify(bad, trusted_public_key_hex=PUB, now=NOW, expected_audience="x").reason
        == "bad_signature"
    )
    other = c.issue(CLAIMS[:1], holder_seed="x", key_hex="cd" * 32, holder_key_hex=HOLDER, now=NOW)
    forged = dict(pres, credential=other.token)  # a credential from another key
    assert not c.verify(forged, trusted_public_key_hex=PUB, now=NOW, expected_audience="x").valid


def test_the_wrong_issuer_key_fails() -> None:
    iss = issued()
    pres = c.present(
        iss.token,
        list(iss.disclosures),
        ["rent_on_time"],
        audience="x",
        holder_key_hex=HOLDER,
        now=NOW,
        nonce=NONCE,
    )
    assert (
        c.verify(
            pres, trusted_public_key_hex=c.public_key_hex("11" * 32), now=NOW, expected_audience="x"
        ).reason
        == "bad_signature"
    )


def test_an_expired_credential_fails() -> None:
    iss = issued()
    pres = c.present(
        iss.token,
        list(iss.disclosures),
        ["rent_on_time"],
        audience="x",
        holder_key_hex=HOLDER,
        now=NOW,
        nonce=NONCE,
    )
    late = NOW + timedelta(days=c.VALIDITY_DAYS, seconds=1)
    assert (
        c.verify(pres, trusted_public_key_hex=PUB, now=late, expected_audience="x").reason
        == "expired"
    )


def test_undisclosed_claims_cannot_be_recovered_from_a_presentation() -> None:
    iss = issued()
    pres = c.present(
        iss.token,
        list(iss.disclosures),
        ["rent_on_time"],
        audience="x",
        holder_key_hex=HOLDER,
        now=NOW,
        nonce=NONCE,
    )
    signed = set(c.verify_payload(pres)["digests"])  # what a holder of the presentation can see
    hidden = CLAIMS[1]  # tuition: only a handful of values are possible, so a guess is easy to try
    for guess_salt in ("", "0" * 32, "salt", hashlib.sha256(b"x").hexdigest()[:32]):
        for on_time in (0, 1, 2):
            for total in (1, 2):
                guess = {"id": hidden["id"], "on_time_payments": on_time, "payments": total}
                assert c.digest(guess_salt, guess) not in signed
    # the digest list also hides how many real claims there are (decoys)
    assert len(signed) == len(CLAIMS) + c.DECOYS


def test_a_disclosure_cannot_be_repeated_or_forged() -> None:
    iss = issued()
    pres = c.present(
        iss.token,
        list(iss.disclosures),
        ["rent_on_time"],
        audience="x",
        holder_key_hex=HOLDER,
        now=NOW,
        nonce=NONCE,
    )
    twice = dict(pres, disclosures=pres["disclosures"] * 2)
    assert (
        c.verify(twice, trusted_public_key_hex=PUB, now=NOW, expected_audience="x").reason
        == "duplicate_disclosure"
    )
    fake = copy.deepcopy(pres)
    fake["disclosures"] = [{"salt": "00", "claim": {"id": "history_months", "months": 99}}]
    assert (
        c.verify(fake, trusted_public_key_hex=PUB, now=NOW, expected_audience="x").reason
        == "claim_not_signed"
    )


def test_presenting_a_claim_that_does_not_exist_is_refused() -> None:
    iss = issued()
    with pytest.raises(ValueError):
        c.present(
            iss.token,
            list(iss.disclosures),
            ["nope"],
            audience="x",
            holder_key_hex=HOLDER,
            now=NOW,
            nonce=NONCE,
        )


def test_issue_needs_unique_claim_ids() -> None:
    with pytest.raises(ValueError):
        c.issue(
            [CLAIMS[0], CLAIMS[0]], holder_seed="x", key_hex=KEY, holder_key_hex=HOLDER, now=NOW
        )
    with pytest.raises(ValueError):
        c.issue([], holder_seed="x", key_hex=KEY, holder_key_hex=HOLDER, now=NOW)


@given(
    st.dictionaries(
        st.text(max_size=8),
        st.one_of(
            st.text(max_size=20), st.integers(), st.none(), st.lists(st.integers(), max_size=3)
        ),
        max_size=6,
    )
)
def test_verify_never_raises_on_garbage(junk: dict) -> None:  # type: ignore[type-arg]
    assert c.verify(junk, trusted_public_key_hex=PUB, now=NOW, expected_audience="x").valid is False


def test_verify_never_raises_on_odd_shapes() -> None:
    iss = issued()
    for pres in (
        {"credential": 5},
        {"credential": "a.b"},
        {"credential": iss.token, "disclosures": "x"},
        {"credential": iss.token, "disclosures": [{"salt": 1}]},
        {"credential": iss.token, "disclosures": [None]},
        {"credential": iss.token, "disclosures": [{}] * 100},
    ):
        assert (
            c.verify(pres, trusted_public_key_hex=PUB, now=NOW, expected_audience="x").valid
            is False
        )


# ---- holder binding: audience, nonce and a holder signature (a forwarded presentation fails)


def shared(audience: str = "landlord A") -> dict:  # type: ignore[type-arg]
    iss = issued()
    return c.present(
        iss.token,
        list(iss.disclosures),
        ["rent_on_time"],
        audience=audience,
        holder_key_hex=HOLDER,
        now=NOW,
        nonce=NONCE,
    )


def check(pres: dict, audience: str = "landlord A", seen: set | None = None) -> c.Verdict:  # type: ignore[type-arg]
    def consume(nonce: str) -> str | None:
        if seen is None:
            return None
        if nonce in seen:
            return "nonce_reused"
        seen.add(nonce)
        return None

    return c.verify(
        pres, trusted_public_key_hex=PUB, now=NOW, expected_audience=audience, consume_nonce=consume
    )


def test_a_presentation_for_another_audience_fails() -> None:
    assert check(shared("landlord A"), audience="landlord A").valid
    assert check(shared("landlord A"), audience="landlord B").reason == "wrong_audience"


def test_changing_the_audience_or_nonce_breaks_the_holder_proof() -> None:
    pres = shared("landlord A")
    forwarded = dict(pres, audience="landlord B")  # a middleman relabels it for the next verifier
    assert check(forwarded, audience="landlord B").reason == "bad_holder_proof"
    renonced = dict(pres, nonce="0" * 32)
    assert check(renonced).reason == "bad_holder_proof"
    retimed = dict(pres, iat="2026-10-12T13:00:00+00:00")
    assert check(retimed).reason == "bad_holder_proof"


def test_a_forwarded_presentation_cannot_be_re_signed_without_the_holder_key() -> None:
    pres = shared("landlord A")
    attacker_key = "ef" * 32  # whoever received the presentation does not hold the user's key
    iss_digests = [c.digest(d["salt"], d["claim"]) for d in pres["disclosures"]]
    forged = dict(pres, audience="landlord B")
    forged["proof"] = c.b64e(
        c.private_key(attacker_key).sign(
            c._proof_body(pres["credential"], "landlord B", pres["nonce"], pres["iat"], iss_digests)
        )
    )
    assert check(forged, audience="landlord B").reason == "bad_holder_proof"


def test_a_reused_nonce_is_refused_after_the_first_acceptance() -> None:
    seen: set = set()  # type: ignore[type-arg]
    pres = shared()
    assert check(pres, seen=seen).valid
    assert check(pres, seen=seen).reason == "nonce_reused"


def test_a_failed_check_does_not_burn_the_nonce() -> None:
    seen: set = set()  # type: ignore[type-arg]
    pres = shared()
    assert check(pres, audience="landlord B", seen=seen).reason == "wrong_audience"
    assert check(pres, seen=seen).valid  # the right verifier can still use it once


def test_a_presentation_without_a_holder_proof_is_refused() -> None:
    pres = shared()
    for key in ("proof", "nonce", "audience"):
        stripped = {k: v for k, v in pres.items() if k != key}
        assert check(stripped).reason == "not_holder_bound"


def test_the_holder_key_is_pinned_inside_the_signed_credential() -> None:
    pres = shared()
    assert c.verify_payload(pres)["cnf"] == c.public_key_hex(HOLDER)
    # a credential issued for a different holder key does not accept this holder's proof
    other = c.issue(CLAIMS, holder_seed="x", key_hex=KEY, holder_key_hex="12" * 32, now=NOW)
    mixed = dict(pres, credential=other.token)
    assert not check(mixed).valid
