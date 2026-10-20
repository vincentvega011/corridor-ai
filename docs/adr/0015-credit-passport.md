# 0015. Credit Passport: a signed credential with selective disclosure

Date: 2026-10-12. Status: accepted (P3-1 Part 4).

## Context
A newcomer without a local credit history needs a way to prove good payment behaviour (rent, tuition, bills, money
sent home) to a new landlord or lender without handing over a statement. `docs/spec/modules.md` 5.13 asked for a
W3C verifiable credential as a JWS with a `did:key` issuer and SD-JWT style disclosure. The P3-1 brief asks for
separate claims, each salted and hashed, signed with Ed25519, with issue, present and verify endpoints.

## Decision
1. **Format.** `corridor.passport.credential`. The credential is `base64url(payload) "." base64url(signature)`; the
   payload (canonical JSON) holds the issuer, a holder pseudonym (a hash, never the user id), issue and expiry
   times (90 days), the issuer public key and a sorted list of claim digests. Each digest is
   `SHA-256(salt "|" canonical(claim))` with a random 128 bit salt, plus three random decoy digests so the number
   of real claims is not visible. The holder keeps the disclosures (`{salt, claim}`). A presentation is the
   credential plus only the chosen disclosures plus the audience. This is SD-JWT in spirit, not in wire format:
   no `did:key` and no JWT library, which keeps verification a short function that a bank reviewer can read.
   Deviation from modules.md 5.13 recorded here (the spec section keeps its text).
2. **Claims** (`corridor.passport.claims`, from `credit_events` and `transfers`, counts only): rent, tuition and
   bills paid on time (k of n), regular remittances (count, months), no failed payments (only if true) and months of
   history. No amounts, account numbers or payee names (INV-12). A late rent month is reported honestly (5 of 6).
3. **Keys.** One synthetic Ed25519 issuer key per sandbox, generated on first use in `secrets/` (mode 0600,
   registered with the prompt guard, never in a prompt, log or response). The public key is public.
4. **Actions.** Issuing (`ISSUE_PASSPORT`, existing) and sharing (`SHARE_PASSPORT`, new, with `PassportShareSpec`:
   passport id, claim ids, audience) are proposals like any other: policy (R-PASSPORT: history needed to issue;
   to share, an own unexpired passport, claims it holds, an audience), a hash bound grant (INV-5) and the executor,
   which recomputes the claims from the database. Issuing needs a confirmation. Sharing needs MFA because personal
   data leaves, and the safety lock blocks it. Both are ledgered (`passport_issued`, new `passport_shared`).
5. **Selection in chat** is deterministic keyword matching on the user's own words, never a model; an unclear claim
   is asked ("which proof"), nothing is guessed.
6. **Endpoints** (`corridor.api.passport`): `GET /passport`, `POST /passport/issue`, `POST /passport/present`,
   `POST /passport/confirm` (grant plus executor, MFA when required), `POST /passport/verify` (public, pure code).
7. **Not a blockchain.** It is a signed credential. Nothing here is on-chain or involves any crypto asset.

## Consequences
Tables `passports` and `passport_presentations` (migration 0010), `LedgerEventType.PASSPORT_SHARED`,
`ActionType.SHARE_PASSPORT`. The hash covers `passport_share` only when present, so other hashes are unchanged.
Limits: no revocation list (a credential lives 90 days, so expiry bounds it), claims are as true as the sandbox
history. Tests: `tests/unit/passport`, `tests/integration/test_passport.py`, tasks t17
and t18.

## Amendment (P3-1b): standard primitives, SD-JWT style, holder binding
8. **What is standard and what is ours.** The primitives are standard: Ed25519 signatures from the
   `cryptography` library and salted SHA-256 digests. The wire format is SD-JWT style for the prototype (signed list
   of salted claim digests, disclosures chosen by the holder, decoy digests), not a standard SD-JWT. A production
   deployment would use a standard **SD-JWT VC** (IETF SD-JWT selective disclosure with key binding, and the VC data
   model) with a verifier issued challenge, instead of our small format.
9. **Binding a presentation to an audience, a nonce and the holder.** The issuer signs the holder's public key into
   the credential (`cnf`). A presentation carries the audience, a random 128 bit nonce, the time and a `proof`:
   the holder's Ed25519 signature over those, a hash of the credential and the digests of the disclosed claims.
   `credential.verify` needs the verifier's own name (`expected_audience`) and refuses: a presentation made for
   another audience (`wrong_audience`), one whose audience, nonce or time was changed (`bad_holder_proof`), one
   without a holder proof (`not_holder_bound`), and, through the verifier's nonce store, one already accepted
   (`nonce_reused`). A forwarded presentation therefore fails at the next verifier: relabelling it breaks the
   proof, and only the holder key can sign a new one. A presentation that fails any other check does not burn its
   nonce. `POST /passport/verify` now takes `expected_audience` (contract change, Python and TS regenerated) and
   records accepted nonces (`passport_nonces`, migration 0011), the one write on that route.
10. **Where the holder key lives.** Per user in `secrets/passport_holder/` (mode 0600, known to the prompt guard,
    never returned). In a real product the key sits on the customer's device and signs there; the sandbox has no
    device, so the server signs when the user confirms the share (MFA, hash bound grant).
11. **Remaining limits.** No revocation list (expiry after 90 days bounds a lost or wrong credential); the nonce is
    chosen by the holder rather than by a verifier challenge, so a presentation is single use per verifier database,
    not fresh against a verifier that keeps no state; a credential issued before this change has no holder key and
    cannot be presented (issue a new one). Tests: `tests/unit/passport/test_credential.py` (wrong audience, reused
    nonce, forwarding, re-signing with another key), `tests/integration/test_passport.py`, task t18 (now eight
    verifier steps).

## Amendment (P3-2): the verifier issues the challenge
12. The nonce is no longer chosen by the holder. A verifier calls `POST /passport/challenge` (audience), the holder
    answers that nonce in its presentation within 10 minutes, and the verifier accepts it once. See ADR 0018 item 5.
    This closes the "holder chosen nonce" limit of item 11 (a chat share, with no verifier, still gets a system
    issued challenge, labelled `issued_by: holder`). Revocation is still not built; expiry bounds it.
