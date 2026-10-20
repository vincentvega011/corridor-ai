# 0014. Tuition funding plan, instalment payments and contribution requests

Date: 2026-10-12. Status: accepted (P3-1 Part 3).

## Context
Priya's semester fee (AUD 6,500.00) is more than her spending account holds (AUD 4,200.00). Three sources can
fund it: the spending account, an AUD savings account (AUD 1,500.00) and a family contribution from her uncle
Ramesh, who holds QAR. Until now an invoice could only be paid in one payment of the exact amount
(R-INVOICE-BENEFICIARY), and an account was identified by its currency.

## Decision
1. **Planner.** `corridor.optimizer.tuition` (OR-Tools CP-SAT) decides how much each source gives and by which
   channel. Hard constraints: the full amount is funded, each own account gives at most its balance (the spending
   account keeps a buffer, `config/funding.yaml`, an assumption), the family request is within the link limit and
   a channel's minimum, per transfer and daily limit, and arrives before the due date. Objective, in order: total
   fees rounded up to whole minor units, least money asked from family, least use of savings, fewest family
   sources. Deterministic: seed 0, one worker, 5 second limit. Only a proven OPTIMAL answer is returned; a
   timeout, an unexpected error or a non-optimal status gives "no plan", never a partial plan (INV-10).
   `brute_force` (tests only) enumerates every allocation of small instances and the solver must match it
   exactly on cost, family money, savings use and family sources. The channel fee arithmetic is the same
   ceiling and floor integer arithmetic as `optimizer/routes.py`, encoded as linear constraints.
2. **Instalments.** R-INVOICE-BENEFICIARY now allows a payment of up to what is still owed
   (`invoices.paid_minor`, status `part_paid`, then `paid`), instead of only the exact amount. The money still
   goes only to the stored beneficiary, an overpayment is denied (policy and executor), a different currency is
   denied, and each instalment has its own idempotency key, card, grant and policy decision. Equal consecutive
   instalments can trigger the R-DUPLICATE step-up (MFA), which is acceptable. Without this a multi source fee
   cannot be paid at all, so the relaxation is deliberate; it does not let money reach any other destination.
3. **Contribution requests.** New action `REQUEST_CONTRIBUTION` (contract change, `ContributionSpec`): asks a
   family member linked by the bank (`family_links`, synthetic, per request limit in the contributor's currency)
   for money into one of the requester's own accounts. It stores a row in `contribution_requests` and moves no
   money: the contributor decides in their own account (a P3-2 customer screen), so the requester can only ask
   (INV-1). R-CONTRIBUTION denies a request with no link, over the limit, in the wrong currency; R-OWNERSHIP
   denies a target account that is not the requester's. The WYSIWYS hash adds the `contribution` terms only when
   present, so every other proposal keeps the same hash. The planner never reads the contributor's balance.
4. **Accounts.** `accounts.kind` (`spending` or `savings`); uniqueness is now (user, currency, kind). Payments
   normally leave from the spending account of the currency; savings are used only through a plan.
5. **Agent.** A `PAY_INVOICE` turn without an attached document runs the `plan_tuition_funding` read tool. A
   one step plan behaves as before. A multi step plan becomes a queue: a request first (the slow thing starts
   first), then payments, each step built, decided by policy, shown and confirmed on its own (`next_step` node
   after each execution). Cancelling or sending a new message drops the rest of the queue. With an attached
   document the old single payment path is used unchanged (document checks are not bypassed). The reply after
   the last step says what is still to be paid once the family's money arrives.

## Consequences
Limits: the family member's acceptance is not built (the request stays `pending`); the due date counts
conservatively in whole days; the planner does not model the buffer returning after the family money arrives;
the savings to spending transfer is avoided by paying from each account directly. OR-Tools is a local library
(see `docs/spec/setup.md`). Tests: `tests/unit/optimizer/test_tuition.py`, `tests/unit/policy/test_rules.py`,
`tests/integration/executor/test_tuition_funding.py`, tasks t04, t15 and t16.
