# A. Route comparison and a confirmed route payment with TOTP

Sandbox: no real money. Synthetic users and accounts. Replay mode, demo clock (10 Oct 2026).

## Steps

- `usr_ramesh` POST `/routes/compare` body keys: from_currency, to_currency, amount_minor, deadline -> HTTP 200 (Routes screen, Compare)
    - recommended route KathmanduLink: cost QAR 18.69, received NPR 33,193.95, eta 4 h; alternatives: ['PeakRemit', 'SwiftHome']
- `usr_ramesh` POST `/chat` body keys: message, action -> HTTP 200 (Routes screen, Send button: no model)
    - intent send_money parser=structured rule=structured
    - route {}
    - policy_decision REQUIRE_MFA rules=['R-TIER-HIGH', 'R-FLOOR'] policy=e562784c5bca.. risk=0
    - confirm_request level=mfa rules=['R-TIER-HIGH', 'R-FLOOR'] hash=2cc44eecfba3.. step=None [action=transfer, amount=QAR 800.00, to=Ama ****4212, from_account=QAR spending account ****4485, deadline=None]
- `usr_ramesh` POST `/chat/86ef674780aa49229077c09a7f7a99ec/confirm` body keys: proposal_hash, totp=<code> -> HTTP 200 (level mfa)
    - executed {"transfer_id": "tr_e287e99f32adc878f7fd", "amount": "QAR 800.00", "payee": "Ama", "rule_ids": ["R-TIER-HIGH", "R-FLOOR"]}
    - grounding {"outcome": "pass", "numbers_checked": 2, "attempts": [{"ok": true, "ungrounded": []}], "facts": {"tool": 2, "user": 1}, "thread_id": "86ef674780aa49229077c09a7
    - message {"text": "Done. QAR 800.00 was sent to Ama. Your new balance is QAR 3,000.00.", "status": "executed"}
- `usr_ramesh` POST `/chat/86ef674780aa49229077c09a7f7a99ec/confirm` body keys: proposal_hash, totp=<code> -> HTTP 409 (replay of the same confirmation)

## Ledger entries produced

6 entries (seq 1 to 6): cascade_decision x1, confirmation_granted x1, execution x1, mfa_attempt x1, policy_decision x1, proposal x1

Decisions in this scenario (policy_decision entries):

- seq 3: REQUIRE_MFA rules ['R-TIER-HIGH', 'R-FLOOR'] policy e562784c5bca..

## Balances before and after (minor units)

| Account | Before | After |
|---|---:|---:|
| acc_ramesh_npr (NPR) | 4500000 | 4500000 |
| acc_ramesh_qar (QAR) | 380000 | 300000 |

Transfers for `usr_ramesh`: 20 before, 21 after.

## Chain verification

`{"valid": true, "entries_checked": 6, "first_broken_seq": null, "reason": null}`

## Expected outcome vs observed

| Claim | Result | Observed |
|---|---|---|
| route comparison returns a recommended route and alternatives | PASS | KathmanduLink plus 2 alternatives |
| the payment button builds a proposal that needs the authenticator | PASS | level mfa, rules ['R-TIER-HIGH', 'R-FLOOR'] |
| the confirmed payment executes | PASS | executed, grounding, message |
| a second confirmation of the same proposal does not pay twice | PASS | HTTP 409, new transfers 1 |
| button flows call no model (every cascade decision is parser=structured) | PASS | 1 cascade decisions, parsers ['structured'] |
