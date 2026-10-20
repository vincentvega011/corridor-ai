# 0018. Bank verification path, typed payees in chat, verifier issued challenge

Date: 2026-10-12. Status: accepted (P3-2 Part 0).

## Context
P3-1b found that a payee Priya types herself cannot receive an AUD 2,000 deposit on the day it is saved
(`R-PAYEE-COOLOFF`, first payment cap NPR 5,000). The owner decided to keep the cap, explain it, and give a
way out that stays under the bank's control. The agent also had no way to save a payee from the user's own
words (only from a document). The passport nonce was chosen by the holder.

## Decision
1. **The cap stays.** The refusal now says why (the account is new), until when (in the user's time zone, zone
   named) and offers "ask the bank to verify this payee" (`R-PAYEE-COOLOFF.first_payment_cap` text, en, ne,
   ne_latn).
2. **Verification request.** `POST /payees/{id}/verification-request` (customer, own payee only, one open
   request per payee) writes a `verification_requests` row and a ledger entry (`verification_requested`).
   It changes nothing about the payee. `GET /officer/verification-requests` lists open requests with the
   Confirmation of Payee result (`match`, `mismatch`, `no_record`). The only way a payee becomes verified is
   still `verify_payee` (officer only, CoP match required, ledgered); it now also closes the request, and a
   refused attempt closes it as `refused` with the reason (the customer may ask again).
3. **A verified payee that the user typed is no longer held.** `R-PAYEE-COOLOFF` treats a verified payee of
   `user_typed` provenance as released: the cap exists for accounts nobody has checked. Verification never
   releases an untrusted origin account (document, message, model): its hold ends only with time (INV-4).
   Without an account key the rule still fails closed. Property tests cover both directions.
   **Trade-off, stated plainly:** a Confirmation of Payee match proves the account holder's name equals the name
   the user typed. A deceived user types the name the scammer gave, and the scammer's account is registered under
   that name, so CoP alone would match. The control is therefore the officer's check (the reason is mandatory and
   ledgered, for example "lease seen"), not the name match. The officer console must show who asked and why.
4. **Typed payee in chat.** Intent `add_payee` with an account number in the user's message produces an
   `ADD_PAYEE` proposal (MFA, `R-PAYEE-ADD`). Origin is decided by code (`corridor.nlu.typed_payee`):
   `USER_TYPED` only if the digits appear verbatim in the user's own typed words; `UNTRUSTED` if they appear only in
   pasted or forwarded text or the split was uncertain; `MODEL_INFERRED` if a model supplied digits found in
   neither (nothing in the shipped agent does; the rule is explicit and tested). The saved row takes the least
   trusted origin of name and number. The proposal and state carry only the keyed hash and the last four digits
   (INV-12). Untrusted and model origins are held in full like document payees. No planner prompt or tool changed,
   so recorded cassettes still match.
5. **Verifier issued challenge.** `POST /passport/challenge` (public, 30 per minute) gives a verifier a one time
   nonce for its audience, valid 10 minutes. The share proposal carries it (it is part of what the user confirms,
   INV-5), the presentation signs it, `POST /passport/verify` accepts it once, for that audience, in time
   (`challenge_unknown`, `nonce_reused`, `wrong_audience`, `challenge_expired`). Expired challenges are deleted when
   a challenge is issued, and at most 5,000 are open. A chat share, with no verifier, gets a challenge issued by
   the system (`issued_by: holder`), which is weaker and labelled. `passport_nonces` is replaced by
   `passport_challenges` (migration 0012).
6. **Smaller items.** Holder keys register with the prompt guard when created. The rate limit key behind a proxy
   comes from a configured header (`TRUSTED_PROXY_HEADER`, empty by default) with the remote address as the
   fallback. A demo clock (`DEMO_CLOCK=on`, dev and demo only) fixes the date to 10 Oct 2026; `GET /clock` tells the
   apps to show a banner.

## Consequences
Migration 0012 (`verification_requests`, `passport_challenges`). New ledger event type `verification_requested`.
Additive API changes only (`docs/spec/api.md`): `PayeeView.hold_until`, `PayeeView.verification`,
`PassportPresentRequest.challenge`, `PassportVerifyRequest.expected_audience` (P3-1b), new routes above.
Tasks t19 (chat typed payee, paid after 24 hours) and t20 (same day: cap, verification request, officer verifies,
MFA payment). Policy change: tests in `tests/properties/test_policy_invariants.py`,
`tests/unit/policy/test_rules.py`, `tests/integration/test_typed_landlord_risk.py`,
`tests/integration/api/test_frozen_api.py`.

## Known risks (security review of P3-2, PASS WITH NOTES)
- A deceived user types the name the scammer gave, the scammer's account is registered under it, so Confirmation of
  Payee matches; the control is the officer's check and mandatory reason, not the name match.
- `POST /api/session` of the customer app lets any visitor pick a demo persona (sandbox feature). It must be removed
  or replaced by a login outside the demo. Cookies are `Secure` only with `COOKIE_SECURE=1`.
- `TRUSTED_PROXY_HEADER` must be set only behind a proxy we run that overwrites it; otherwise a client can spoof its
  rate limit key. A public challenge route can be filled to its cap (5,000) from many addresses: availability only.
- A typed 6 to 8 digit account number is not covered by the 9 digit prompt guard if the rule path does not take the
  message; the raw typed text is also in the checkpoint. A production deployment must protect the checkpoint store.
- `LLM_RECORD_ROLES` is now refused outside dev, demo, test and eval.
