# F. Ledger chain verification, tamper detection, restore, reset

Sandbox: no real money. Synthetic users and accounts. Replay mode, demo clock (10 Oct 2026).

## Steps

- officer GET /officer/ledger/verify -> `{"valid": true, "entries_checked": 13, "first_broken_seq": null, "reason": null}`
- tamper: entry 6 changed with the append-only trigger switched off (scratch database, administrator connection)
- officer GET /officer/ledger/verify -> `{"valid": false, "entries_checked": 6, "first_broken_seq": 6, "reason": "payload does not match payload_hash"}`
- the original row is restored -> `{"valid": true, "entries_checked": 13, "first_broken_seq": null, "reason": null}`

## Ledger entries produced

13 entries (seq 1 to 13): cascade_decision x2, confirmation_granted x1, execution x2, mfa_attempt x1, policy_decision x2, proposal x2, reader_extraction x2, verification_requested x1

Decisions in this scenario (policy_decision entries):

- seq 5: REQUIRE_MFA rules ['R-INJECTION', 'R-PAYEE-ADD', 'R-FLOOR'] policy e562784c5bca..
- seq 11: DENY rules ['R-PAYEE-COOLOFF', 'R-RISK', 'R-TAINT-PAYEE', 'R-TIER-HIGH', 'R-FLOOR'] policy e562784c5bca..

## Balances before and after (minor units)

| Account | Before | After |
|---|---:|---:|
| acc_priya_aud (AUD) | 420000 | 420000 |
| acc_priya_aud_savings (AUD) | 150000 | 150000 |
| acc_priya_npr (NPR) | 62000000 | 62000000 |

Transfers for `usr_priya`: 8 before, 8 after.

## Chain verification

`{"valid": true, "entries_checked": 13, "first_broken_seq": null, "reason": null}`

## Expected outcome vs observed

| Claim | Result | Observed |
|---|---|---|
| the hash chain verifies before any tampering | PASS | {"valid": true, "entries_checked": 13, "first_broken_seq": null, "reason": null} |
| verification FAILS after one stored entry is altered | PASS | {"valid": false, "entries_checked": 6, "first_broken_seq": 6, "reason": "payload does not match payload_hash"} |
| the first broken link is reported | PASS | first_broken_seq 6 |
| restoring the row makes the chain verify again | PASS | {"valid": true, "entries_checked": 13, "first_broken_seq": null, "reason": null} |

## Reset

The sandbox was reset after the run (`demo_reset.reset`): 0 ledger entries remain.
