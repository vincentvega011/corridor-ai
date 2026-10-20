# B. Tuition plan: request to the uncle, he accepts, step 2 paid with TOTP

Sandbox: no real money. Synthetic users and accounts. Replay mode, demo clock (10 Oct 2026).

## Steps

- `usr_priya` GET `/tuition/plan?invoice_id=inv_priya_tuition_2026s2` -> HTTP 200 (Tuition screen, plan)
    - plan steps: 3
- `usr_priya` POST `/chat` body keys: message, action -> HTTP 200 (Tuition screen, Start: no model)
    - intent pay_invoice parser=structured rule=structured
    - route {"status": "ok"}
    - policy_decision REQUIRE_CONFIRM rules=['R-FLOOR', 'R-TIER-LOW'] policy=e562784c5bca.. risk=0
    - confirm_request level=confirm rules=['R-FLOOR', 'R-TIER-LOW'] hash=aa45b59f6817.. step={'n': 1, 'total': 3} [action=request_contribution, amount=QAR 3,332.55, to=Ramesh (uncle, QA) (a request, no money moves from you), from_account=into AUD spending account ****9873, arrives=AUD 1,300.00 via PeakRemit, about 24 hours after they agree]
- `usr_priya` POST `/chat/ad4fd9bbff95450e89992f63c6d3d1f7/confirm` body keys: proposal_hash -> HTTP 200 (step 1)
    - executed {"amount": "QAR 3,332.55", "rule_ids": ["R-FLOOR", "R-TIER-LOW"]}
    - policy_decision REQUIRE_MFA rules=['R-TIER-HIGH', 'R-FLOOR'] policy=e562784c5bca.. risk=0
    - confirm_request level=mfa rules=['R-TIER-HIGH', 'R-FLOOR'] hash=8bc4a05d77f2.. step={'n': 2, 'total': 3} [action=pay_invoice, amount=AUD 3,700.00, to=Harbourview University ****2125, from_account=AUD spending account ****9873, invoice=Harbourview University, Semester 2, 2026 (due 2026-10-31)]
- `usr_ramesh` GET `/contributions` -> HTTP 200 (Family screen)
    - incoming request: {"request_id": "cr_839d2c30b426bf9a15fc", "id": null, "status": "pending", "amount": "QAR 3,332.55", "amount_minor": null, "currency": "QAR"}
- `usr_ramesh` POST `/contributions/cr_839d2c30b426bf9a15fc/respond` body keys: accept -> HTTP 200 (Ramesh accepts)
- `usr_priya` POST `/chat/ad4fd9bbff95450e89992f63c6d3d1f7/confirm` body keys: proposal_hash, totp=<code> -> HTTP 200 (step 2)
    - executed {"transfer_id": "tr_00236475c8005037457d", "amount": "AUD 3,700.00", "payee": "pay_priya_university", "rule_ids": ["R-TIER-HIGH", "R-FLOOR"]}
    - policy_decision REQUIRE_MFA rules=['R-TIER-HIGH', 'R-FLOOR'] policy=e562784c5bca.. risk=0
    - confirm_request level=mfa rules=['R-TIER-HIGH', 'R-FLOOR'] hash=7bdad942a5bf.. step={'n': 3, 'total': 3} [action=pay_invoice, amount=AUD 1,500.00, to=Harbourview University ****2125, from_account=AUD savings account ****9804, invoice=Harbourview University, Semester 2, 2026 (due 2026-10-31)]

Step 3 is left unconfirmed on purpose: it needs its own confirmation and nothing pays it automatically.

## Ledger entries produced

13 entries (seq 1 to 13): cascade_decision x1, confirmation_granted x2, contribution_responded x1, execution x2, mfa_attempt x1, policy_decision x3, proposal x3

Decisions in this scenario (policy_decision entries):

- seq 3: REQUIRE_CONFIRM rules ['R-FLOOR', 'R-TIER-LOW'] policy e562784c5bca..
- seq 7: REQUIRE_MFA rules ['R-TIER-HIGH', 'R-FLOOR'] policy e562784c5bca..
- seq 13: REQUIRE_MFA rules ['R-TIER-HIGH', 'R-FLOOR'] policy e562784c5bca..

## Balances before and after (minor units)

| Account | Before | After |
|---|---:|---:|
| acc_priya_aud (AUD) | 420000 | 50000 |
| acc_priya_aud_savings (AUD) | 150000 | 150000 |
| acc_priya_npr (NPR) | 62000000 | 62000000 |
| acc_ramesh_npr (NPR) | 4500000 | 4500000 |
| acc_ramesh_qar (QAR) | 380000 | 380000 |

Transfers for `usr_priya`: 8 before, 9 after.

Transfers for `usr_ramesh`: 20 before, 20 after.

## Chain verification

`{"valid": true, "entries_checked": 13, "first_broken_seq": null, "reason": null}`

## Expected outcome vs observed

| Claim | Result | Observed |
|---|---|---|
| step 1 is a contribution request that moves no money | PASS | plan_step {'n': 1, 'total': 3}, level confirm |
| step 1 moved no money | PASS | Priya's balances unchanged after the request |
| the contributor accepts as Ramesh (no money moves by accepting) | PASS | status accepted |
| step 2 is a payment proposal with its own hash | PASS | step {'n': 2, 'total': 3}, level mfa |
| step 2 executes after the TOTP | PASS | executed, ledger_append, ledger_append, policy_decision, confirm_request |
| button flows call no model (every cascade decision is parser=structured) | PASS | 1 cascade decisions, parsers ['structured'] |
