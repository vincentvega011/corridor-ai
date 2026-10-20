"""Credit Passport credential: Ed25519 signature, salted claim digests, selective disclosure.

This is a signed credential, in the style of SD-JWT, and nothing more. It is not a blockchain, it
is not on-chain and it involves no crypto asset: the issuer signs a small JSON document with an
Ed25519 key and anyone with the issuer's public key can check it with plain code.

Format (all JSON is canonical, ``corridor.contracts.canonical``):

* The **credential** is ``base64url(payload) "." base64url(signature)``. The payload holds the
  issuer, a holder pseudonym (never the user id), issue and expiry times and a sorted list of claim
  **digests**. Each digest is ``SHA-256(salt || canonical(claim))`` with a random 128 bit salt, plus
  a few random decoy digests, so the verifier cannot tell how many real claims exist.
* A **disclosure** is ``{"salt": ..., "claim": {...}}``. The holder keeps all of them.
* A **presentation** is the credential plus only the disclosures the holder chooses, plus the
  audience it is meant for, a random nonce, the time, and a **holder proof**: an Ed25519 signature,
  made with the holder's own key, over the audience, the nonce, the time, a hash of the credential
  and the digests of the disclosed claims. The issuer signs the holder's public key into the
  credential (``cnf``), so only the holder can make a proof. A presentation forwarded to
  someone else
  keeps its audience (changing it breaks the proof), and a presentation seen twice is refused by a
  verifier that records nonces. The verifier recomputes each digest from a disclosure and checks it
  is in the signed list. A claim that is not disclosed cannot be recovered: its salt is random and
  never leaves the holder, so the digest cannot be guessed even for a claim with few possible
  values.

The primitives are standard (Ed25519 from the ``cryptography`` library, salted SHA-256 digests); the
format is SD-JWT style for the prototype. A production deployment would use a standard SD-JWT VC
with a holder key binding and a verifier issued challenge (ADR 0015).

Verification is pure code: no database, no network, no model (INV-2 spirit for the verifier).
The one
piece of state, "has this nonce been seen", is passed in by the caller as a function.
"""

import base64
import hashlib
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from corridor.contracts.canonical import canonical_bytes

ISSUER = "Corridor AI sandbox bank (synthetic)"
VERSION = 1
DECOYS = 3
VALIDITY_DAYS = 90
MAX_DISCLOSURES = 32
MAX_CLOCK_SKEW = timedelta(minutes=5)


def b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def digest(salt: str, claim: dict[str, Any]) -> str:
    """The signed fingerprint of one claim: SHA-256 over the salt and the canonical claim."""
    return hashlib.sha256(salt.encode() + b"|" + canonical_bytes(claim)).hexdigest()


def private_key(hex_key: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(hex_key))


def public_key_hex(hex_key: str) -> str:
    from cryptography.hazmat.primitives import serialization

    pub = private_key(hex_key).public_key()
    return pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


@dataclass(frozen=True)
class Issued:
    """What issuing returns: the signed credential and every disclosure (kept by the holder)."""

    token: str
    disclosures: tuple[dict[str, Any], ...]
    holder: str
    expires_at: datetime
    claim_ids: tuple[str, ...] = field(default=())


def issue(
    claims: list[dict[str, Any]],
    *,
    holder_seed: str,
    key_hex: str,
    holder_key_hex: str,
    now: datetime,
    rand: Any = secrets,
) -> Issued:
    """Sign ``claims`` (each a dict with an ``id``). ``holder_seed`` makes the pseudonym: a hash,
    so the credential never carries the user id. ``holder_key_hex`` is the holder's private
    key: only
    its public half (``cnf``) goes into the signed payload, so presentations can be bound to
    the holder."""
    if not claims or len({c["id"] for c in claims}) != len(claims):
        raise ValueError("claims must be non empty with unique ids")
    disclosures = [{"salt": rand.token_hex(16), "claim": c} for c in claims]
    digests = [digest(d["salt"], d["claim"]) for d in disclosures]
    digests += [hashlib.sha256(rand.token_bytes(16)).hexdigest() for _ in range(DECOYS)]
    holder = "holder:" + hashlib.sha256(holder_seed.encode()).hexdigest()[:16]
    expires = now + timedelta(days=VALIDITY_DAYS)
    payload = {
        "v": VERSION, "iss": ISSUER, "sub": holder, "iat": now.astimezone(UTC).isoformat(),
        "exp": expires.astimezone(UTC).isoformat(), "alg": "EdDSA",
        "pub": public_key_hex(key_hex), "cnf": public_key_hex(holder_key_hex),
        "digests": sorted(digests),
    }  # fmt: skip
    body = canonical_bytes(payload)
    sig = private_key(key_hex).sign(body)
    return Issued(
        f"{b64e(body)}.{b64e(sig)}",
        tuple(disclosures),
        holder,
        expires,
        tuple(c["id"] for c in claims),
    )


def _proof_body(token: str, audience: str, nonce: str, iat: str, digests: list[str]) -> bytes:
    """What the holder signs: who it is for, the nonce, the time, which credential, which claims."""
    return canonical_bytes(
        {
            "aud": audience, "nonce": nonce, "iat": iat,
            "cred": hashlib.sha256(token.encode()).hexdigest(), "disclosed": sorted(digests),
        }
    )  # fmt: skip


def present(
    token: str,
    disclosures: list[dict[str, Any]],
    claim_ids: list[str],
    *,
    audience: str,
    nonce: str,
    holder_key_hex: str,
    now: datetime,
) -> dict[str, Any]:
    """Build a presentation that discloses only ``claim_ids``, bound to ``audience`` and to the
    verifier's challenge ``nonce``, and signed by the holder key. Anything not chosen stays
    hidden."""
    chosen = [d for d in disclosures if d["claim"]["id"] in set(claim_ids)]
    if len(chosen) != len(set(claim_ids)):
        raise ValueError("a requested claim does not exist")
    iat = now.astimezone(UTC).isoformat()
    body = _proof_body(token, audience, nonce, iat, [digest(d["salt"], d["claim"]) for d in chosen])
    proof = b64e(private_key(holder_key_hex).sign(body))
    return {
        "credential": token, "disclosures": chosen, "audience": audience, "nonce": nonce,
        "iat": iat, "proof": proof,
    }  # fmt: skip


@dataclass(frozen=True)
class Verdict:
    valid: bool
    reason: str | None = None
    claims: tuple[dict[str, Any], ...] = ()
    audience: str | None = None
    holder: str | None = None
    expires_at: str | None = None
    nonce: str | None = None


def verify_payload(presentation: dict[str, Any]) -> dict[str, Any]:
    """The signed payload of a presentation (unverified; for display and tests only)."""
    payload: dict[str, Any] = json.loads(b64d(presentation["credential"].split(".")[0]))
    return payload


def verify(
    presentation: dict[str, Any],
    *,
    trusted_public_key_hex: str,
    now: datetime,
    expected_audience: str,
    consume_nonce: Callable[[str], str | None] | None = None,
) -> Verdict:
    """Check a presentation: signature by the trusted issuer key, not expired, every disclosed claim
    matches a signed digest and appears once, the holder proof is valid under the key the
    issuer signed
    in, and the presentation is meant for ``expected_audience`` (the verifier says who it is, so a
    presentation made for someone else fails). ``consume_nonce`` checks that the nonce is a live
    challenge the verifier issued for this audience, records it as used and returns None, or returns
    the reason (``nonce_reused``, ``challenge_unknown``, ``challenge_expired``). It runs last, so a
    presentation that fails any other check never burns a nonce.
    Never raises: any malformed input is ``invalid``."""
    try:
        token = presentation["credential"]
        disclosures = presentation.get("disclosures", [])
        if not isinstance(token, str) or token.count(".") != 1:
            return Verdict(False, "malformed_credential")
        if not isinstance(disclosures, list) or len(disclosures) > MAX_DISCLOSURES:
            return Verdict(False, "malformed_disclosures")
        body_b64, sig_b64 = token.split(".")
        body, sig = b64d(body_b64), b64d(sig_b64)
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(trusted_public_key_hex)).verify(
                sig, body
            )
        except InvalidSignature:
            return Verdict(False, "bad_signature")
        payload = json.loads(body)
        if payload.get("pub") != trusted_public_key_hex or payload.get("v") != VERSION:
            return Verdict(False, "untrusted_issuer")
        if datetime.fromisoformat(payload["exp"]) <= now:
            return Verdict(False, "expired")
        signed = set(payload["digests"])
        seen: set[str] = set()
        claims = []
        for d in disclosures:
            fingerprint = digest(str(d["salt"]), d["claim"])
            if fingerprint not in signed:
                return Verdict(False, "claim_not_signed")
            if fingerprint in seen:
                return Verdict(False, "duplicate_disclosure")
            seen.add(fingerprint)
            claims.append(d["claim"])
        cnf = payload.get("cnf")
        audience, nonce, iat = (
            presentation.get("audience"),
            presentation.get("nonce"),
            presentation.get("iat"),
        )
        proof = presentation.get("proof")
        if not isinstance(cnf, str) or not all(
            isinstance(x, str) and x for x in (audience, nonce, iat, proof)
        ):
            return Verdict(False, "not_holder_bound")
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(cnf)).verify(
                b64d(str(proof)),
                _proof_body(token, str(audience), str(nonce), str(iat), sorted(seen)),
            )
        except InvalidSignature:
            return Verdict(False, "bad_holder_proof")
        if datetime.fromisoformat(str(iat)) > now + MAX_CLOCK_SKEW:
            return Verdict(False, "from_the_future")
        if audience != expected_audience:
            return Verdict(False, "wrong_audience")
        if consume_nonce is not None:
            refused = consume_nonce(str(nonce))  # records the challenge as used, or says why not
            if refused is not None:
                return Verdict(False, refused)
        return Verdict(
            True, None, tuple(claims), str(audience), payload["sub"], payload["exp"], str(nonce)
        )  # fmt: skip
    except Exception:  # malformed input is never valid (fail closed)
        return Verdict(False, "malformed")
