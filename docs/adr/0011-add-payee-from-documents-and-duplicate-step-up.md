# 0011. Add-payee from documents, and a step-up for repeated payments

Date: 2026-10-11. Status: accepted (P2-6).

## Context
P2-5 found two gaps. A document or message could never become a payee, so the 24 hour hold for an untrusted
account (ADR 0006 amendment) was never reached in the first-time accommodation deposit scam: the agent only asked
a clarification. And two separate messages asking for the same payment were two proposals with two idempotency
keys, so INV-6 did not see a repeat (RT-036).

## Decision
1. **Add-payee flow.** When an attached document names an account that is not a stored payee and the user did not
   name a stored payee, the agent builds an `ADD_PAYEE` proposal (never a payment). Every term is `UNTRUSTED`, and
   the proposal carries a placeholder (`doc_acct_<hash16>`), never the account number (INV-12). Policy asks for MFA
   with a scam warning (`R-PAYEE-ADD`). The executor creates the payee only after re-reading the stored document
   (name, currency, account hash and last four digits must match the proposal), writes it `untrusted`, unverified,
   `added_via=document`, and refuses a second row for the same account. A confirmation, even with MFA, cannot change
   its origin: provenance is written by the executor from the stored document. `R-PAYEE-COOLOFF` then holds it for
   24 hours (cap 0); only time ends the hold. The reply names the earliest allowed time and carries the warning;
   the proposal, policy decision and execution are in the ledger. The full account number is never held after the
   reader (it stores an HMAC and the last four digits).
2. **`R-DUPLICATE`.** The same amount and currency to the same destination account (by hash) within 24 hours needs
   MFA (a fresh authenticator code: TOTP replay protection already refuses a reused code), and the reason names the
   earlier payment (time and amount). It is a step-up, not a denial, not a high risk rule, and never counts toward
   the safety lock. It applies to transfers.

## Consequences
The cautious simulated user now heeds a card that carries `R-DUPLICATE` (a warning a careful user would heed);
the fooled user still confirms, so a deceived user can still make a repeat payment, with a new code. That is the
intended trade-off: friction and visibility, not a ban. New tests: `tests/integration/executor/test_add_payee.py`,
`tests/integration/agent/test_add_payee_flow.py`, `tests/unit/policy/test_duplicate.py`.

## Notes from review (P2-6)
- A poisoned invoice whose issuer text does not match a stored invoice has no `matched_invoice_id`, so it becomes an
  add-payee proposal (MFA, then a 24 hour full hold) instead of an `R-INVOICE-BENEFICIARY` denial. Safe, but a held
  attacker payee then exists, and the evaluation counts it as an attack success (`payee_created`).
- The smoke test `test_cousin_screenshot_cannot_move_money...` changed on purpose: a document account can now produce an
  `add_payee` card (never a payment). It still asserts nothing executes and no transfer exists.
- Open: the payee `display_name` of a document-derived payee is untrusted text up to 120 characters returned by
  `get_payees`. No traced path puts it in a planner or composer prompt; a test asserting that is still to be written.

## Notes from the P2-7 review
- After the hold an unverified untrusted-origin account can still receive about NPR 5,000 a day with MFA until it is
  verified; "contained" means held and capped, not permanently safe (docs/threat-model.md).
- Open: `get_payees` returns `display_name` raw for document-derived payees; a provenance column or fixed label for it
  (docs/spec/modules.md 5.1) is still to do. Unconvertible currencies add 0 to the unauthorized NPR total (no FX rate).
