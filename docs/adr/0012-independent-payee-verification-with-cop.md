# 0012. Independent payee verification with a simulated Confirmation of Payee

Date: 2026-10-11. Status: accepted (P2-8).

## Context
An untrusted payee (saved from a document) stays capped at NPR 5,000 per 24 hours with MFA after the 24 hour hold
(ADR 0006, P2-7), with no way to lift that. The user cannot be the one to lift it: a deceived user would verify
the scammer. Verification has to be independent of the user and of any model.

## Decision
1. **Simulated Confirmation of Payee (CoP).** `cop_registry` (migration 0007, `data/seeds/cop.yaml`, all synthetic)
   holds a registered holder name per account hash. The CityNest scam account is registered to "Marcus Tan Holdings"
   (does not match the document name "CityNest Bookings"); a genuine landlord account is registered to "Wattle Lane
   Property Pty Ltd" (matches "Wattle Lane Property"). `sandbox/cop.names_match` is deterministic: lower case words,
   company suffixes ignored, identical word sets or one contained in the other with at least two words.
2. **`verify_payee` is an officer action.** `executor/verify.py`, reachable only through
   `POST /officer/payees/{id}/verify` with the bank officer token (a secret no customer token equals). It is not an
   `ActionType`, not a read tool, not importable from `corridor.agent` (tests assert this), so no model and no chat
   path can call it (INV-1). It needs a CoP match: a mismatch or a missing record refuses and writes a ledger
   `rejection` with the officer id and reason; success writes an `execution` entry (officer id, reason, CoP match) and
   sets `verified_at`. It does not change provenance: the payee stays `untrusted` (so MFA still applies); verification
   only ends the unverified cap of `R-PAYEE-COOLOFF`.
3. **The customer cannot verify.** No customer route, no settings route and no chat flow writes `verified_at`
   (`tests/unit/test_verified_only_by_officer.py` scans the source for it). The API test posts with no token, a
   customer token and a wrong token: all 401.
4. **Display.** `get_payees` returns a provenance flag and a fixed label ("Payee from document ****2208") for
   document-derived payees, never the raw name. Deterministic code reads the sanitised name from the database for
   matching what the user typed and for the confirmation card, which shows it as "Name taken from document, not
   verified" (en, ne, ne_latn). The name never enters a prompt.

## Consequences
A genuine new landlord works end to end: saved and held, the officer verifies after a CoP match, payment succeeds
after the hold with MFA (task `t11_priya_genuine_landlord_deposit`). A scam whose registered holder does not match can
never be verified, whatever the officer types. The bank console (P3) gets a verification screen; until then the
action is an API route. CoP is a simulation and says nothing about a real scheme.

## Notes from the P2-8 review
Fixed in P2-8: `names_match` is stricter (equal word sets after removing legal forms only, or the payee name contains
every word of a holder name of two or more words; "Marcus Tan" never matches "Marcus Tan Holdings"); `get_invoice` shows
the fixed label for a document-derived beneficiary; the officer route is on only in dev and demo; every eval case
starts from the real clock.
Open: verification is ledgered as `execution` / `rejection` with actor `system` and the officer id in the payload (a
distinct event type or an officer actor is a P3 contract change; counters must not read it as a money movement); the
officer route has no rate limit and every holder of the token is `officer_demo_1`; `state["payee"]` carries the sanitised
name in the checkpoint (never in a prompt); t11 is not a baseline comparison (the baseline has no verification step).
