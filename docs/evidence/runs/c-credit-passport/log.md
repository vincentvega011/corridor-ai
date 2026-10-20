# C. Credit Passport: issue, verifier challenge, valid presentation, forwarded copy rejected

Sandbox: no real money. Synthetic users and accounts. Replay mode, demo clock (10 Oct 2026).

## Steps

- `usr_priya` POST `/passport/issue` body keys:  -> HTTP 200 (Passport screen, Issue)
    - proposal level confirm, hash 37df8f7d8abd..
- `usr_priya` POST `/passport/confirm` body keys: proposal_hash, totp -> HTTP 200 (confirm issue)
- `usr_priya` GET `/passport` -> HTTP 200
- `verifier` POST `/passport/challenge` body keys: audience -> HTTP 200 (landlord's verifier page, no login)
    - challenge nonce issued (length 32), valid until 2026-10-10T03:25:31.077679+00:00
- `usr_priya` POST `/passport/present` body keys: passport_id, claims, audience, challenge -> HTTP 200 (share one claim: rent_on_time)
- `usr_priya` POST `/passport/confirm` body keys: proposal_hash, totp=<code> -> HTTP 200 (confirm share)
- `verifier` POST `/passport/verify` body keys: presentation, expected_audience -> HTTP 200 (verifier checks the presentation)
    - verifier result: valid=True reason=None
- `verifier` POST `/passport/verify` body keys: presentation, expected_audience -> HTTP 200 (the presentation forwarded to another landlord)
    - verifier result: valid=False reason=wrong_audience
- `verifier` POST `/passport/verify` body keys: presentation, expected_audience -> HTTP 200 (the same presentation presented twice)
    - verifier result: valid=False reason=nonce_reused

## Ledger entries produced

9 entries (seq 1 to 9): confirmation_granted x2, mfa_attempt x1, passport_issued x1, passport_shared x1, policy_decision x2, proposal x2

Decisions in this scenario (policy_decision entries):

- seq 2: REQUIRE_CONFIRM rules ['R-FLOOR', 'R-TIER-LOW'] policy e562784c5bca..
- seq 6: REQUIRE_MFA rules ['R-TIER-HIGH', 'R-FLOOR'] policy e562784c5bca..

## Balances before and after (minor units)

| Account | Before | After |
|---|---:|---:|
| acc_priya_aud (AUD) | 420000 | 420000 |
| acc_priya_aud_savings (AUD) | 150000 | 150000 |
| acc_priya_npr (NPR) | 62000000 | 62000000 |

Transfers for `usr_priya`: 8 before, 8 after.

## Chain verification

`{"valid": true, "entries_checked": 9, "first_broken_seq": null, "reason": null}`

## Expected outcome vs observed

| Claim | Result | Observed |
|---|---|---|
| the passport is issued after the confirmation | PASS | HTTP 200, kind issued |
| a presentation is produced for exactly one claim and one audience | PASS | claims ['rent_on_time'], audience Mr Okafor |
| the intended verifier accepts the presentation | PASS | valid=True reason=None |
| a forwarded presentation is rejected | PASS | valid=False reason=wrong_audience |
| a replayed presentation is rejected (nonce used once) | PASS | valid=False reason=nonce_reused |
