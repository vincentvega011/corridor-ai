# Policy rules

Source of truth: `backend/src/corridor/policy/rules.py` (logic), `backend/config/policy.yaml` (values) and
`data/lang/<code>/policy_reasons.yaml` (user text in en, ne and romanised ne). This page is checked against
the code by `backend/tests/unit/policy/test_rules.py`. See ADR 0006.

## Decision model

`evaluate(proposal, state, config, now)` is a pure function (INV-2). It returns a `PolicyDecision`:
`decision`, `rule_ids` (every rule that fired, most restrictive first), `reasons` (reason keys of the rules
that decided it) and `policy_version_hash`. Decisions are ordered `ALLOW < REQUIRE_CONFIRM < REQUIRE_MFA <
DENY`. Each rule runs on its own and the result is the maximum, so no rule can relax another (INV-7).

- An `ActionProposal` is **never** `ALLOW`: `R-FLOOR` gives every action at least `REQUIRE_CONFIRM`.
- `ALLOW` exists only for read tools (`evaluate_read`, rule `R-TIER-READ`).
- An unknown action is `DENY` (`R-UNKNOWN`, INV-10).
- The reason text of a `DENY` lists only the denying rules; otherwise `R-FLOOR` is hidden when another rule
  explains the decision. `rule_ids` always lists everything that fired, for the audit trail.
- "Outgoing" actions are transfer, pay_invoice, convert and schedule. `R-LOCK` also covers add_payee.
- Amount tiers use NPR equivalents: `amount_minor x fx_to_npr` from the FX snapshot inside `AccountState`,
  rounded up so a limit is never under counted. A missing rate denies (`R-CURRENCY.no_fx_rate`).
- Provenance seen by policy is the lowest of what the proposal carries and what the database stores for the
  payee or invoice, so stored taint cannot be hidden by the proposal (INV-4). `USER_CONFIRMED` counts as
  typed. `UNTRUSTED` and `MODEL_INFERRED` are "tainted".

## Rules

| Rule ID | Applies to | Logic | Decision | Reason keys |
|---|---|---|---|---|
| `R-TIER-READ` | read tools (`evaluate_read`) | tool is in `read_tools` | ALLOW (unknown tool: DENY) | `read_only`, `unknown_tool` |
| `R-FLOOR` | every action | always | REQUIRE_CONFIRM | `confirmation_always` |
| `R-TIER-LOW` | outgoing | amount at or below `limits.low_npr_minor`, payee verified and older than `new_payee_days`, nothing tainted | REQUIRE_CONFIRM | `within_low_limit` |
| `R-TIER-LOW` | cancel_schedule, issue_passport, set_budget | state change that does not move money | REQUIRE_CONFIRM | `state_change` |
| `R-TIER-HIGH` | outgoing | amount above the low limit | REQUIRE_MFA | `amount_above_low_limit` |
| `R-TIER-HIGH` | schedule | creating a schedule | REQUIRE_MFA | `schedule` |
| `R-TIER-HIGH` | payee actions | payee added fewer than `new_payee_days` (30) days ago | REQUIRE_MFA | `new_payee` |
| `R-TIER-HIGH` | payee actions | payee not verified | REQUIRE_MFA | `unverified_payee` |
| `R-DAILY-LIMIT` | outgoing | executed outgoing in the last 24 hours (rolling) plus this amount, in NPR, above `limits.daily_npr_minor`; an earlier amount with no FX rate also denies | DENY | `daily_limit_exceeded`, `daily_total_unknown` |
| `R-VELOCITY` | transfer, pay_invoice | executed transfers in the last `velocity.window_minutes` plus this one: more than `mfa_over` | REQUIRE_MFA | `velocity_mfa` |
| `R-VELOCITY` | transfer, pay_invoice | more than `deny_over` | DENY | `velocity_deny` |
| `R-TAINT-PAYEE` | payee actions | payee provenance (proposal or stored) tainted | REQUIRE_MFA | `tainted_payee` |
| `R-TAINT-PAYEE` | outgoing | source account provenance tainted | REQUIRE_MFA | `tainted_source_account` |
| `R-TAINT-PAYEE` | pay_invoice | tainted payee that is not the invoice's stored beneficiary | DENY | `tainted_beneficiary_differs` |
| `R-TAINT-AMOUNT` | any with an amount | amount (or the stored invoice) `UNTRUSTED` | REQUIRE_CONFIRM at minimum | `untrusted_amount` |
| `R-TAINT-AMOUNT` | any with an amount | amount `MODEL_INFERRED` (stricter than the spec) | REQUIRE_MFA | `inferred_amount` |
| `R-INJECTION` | any | a `derived_from_docs` hash is flagged `injection_suspected` | REQUIRE_MFA | `injection_suspected` |
| `R-RISK` | any with a risk score | score at or above `risk.high_bps` | REQUIRE_MFA | `risk_high` |
| `R-RISK` | any with a risk score | score at or above `risk.critical_bps` (also locks the session) | DENY | `risk_critical` |
| `R-PAYEE-ADD` | add_payee | always | REQUIRE_MFA | `payee_add_mfa` |
| `R-PAYEE-ADD` | add_payee | any new payee field tainted (from a document, a message or a model) | REQUIRE_MFA, with a scam warning (urgency, a changed account, a request for secrecy) in the user's language | `payee_add_untrusted_warning` |
| `R-PAYEE-ADD` | add_payee | no spec | DENY | `payee_add_incomplete` |
| `R-PAYEE-COOLOFF` | payee actions (transfer, pay_invoice, schedule) | the destination account was first saved less than `payee_cooloff.hours` (24) ago and this payment plus what it already received would exceed the cap: `payee_cooloff.first_payment_cap_npr_minor` (NPR 5,000.00 equivalent) for an account the user typed, `payee_cooloff.untrusted_origin_cap_npr_minor` (0: a full hold) when any row with that account hash has untrusted or model-inferred provenance; the age and the total belong to the account (hash) over every payee row with that hash | DENY, with the earliest allowed time in the reason, whatever the grant or MFA | `first_payment_cap`, `first_payment_cap_untrusted` |
| `R-DUPLICATE` | transfer | the same amount and currency was paid to the same destination account (hash) in the last 24 hours | REQUIRE_MFA (a step-up, never a denial, no lock count); the reason names the earlier payment time and amount | `same_payment@<time>\|<amount minor>\|<currency>` |
| `R-LOCK` | outgoing and add_payee | session locked (INV-11) | DENY | `locked` |
| `R-SETTINGS` | set_budget | category contains a word from `protected_setting_words` (limit, mfa, otp, password, pin, contact, email, phone, address) | DENY | `settings_change` |
| `R-CURRENCY` | outgoing | account currency differs from the amount currency | DENY | `account_currency_mismatch` |
| `R-CURRENCY` | payee actions | payee currency differs and no active provider corridor exists | DENY | `corridor_unsupported` |
| `R-CURRENCY` | payee actions | route does not deliver in the payee currency, or a cross currency payment has no route | DENY | `route_currency_mismatch`, `route_missing` |
| `R-CURRENCY` | convert | pair has no FX rate | DENY | `pair_unsupported` |
| `R-CURRENCY` | any with an amount | no FX rate to NPR for the amount currency | DENY | `no_fx_rate` |
| `R-BALANCE` | transfer, pay_invoice, convert | balance below the amount | DENY | `insufficient_funds` |
| `R-OWNERSHIP` (added) | outgoing, cancel_schedule | source account missing, closed or not the user's | DENY | `account_not_usable` |
| `R-OWNERSHIP` (added) | payee actions | payee not among the user's payees | DENY | `payee_not_found` |
| `R-OWNERSHIP` (added) | pay_invoice | invoice not among the user's open invoices | DENY | `invoice_not_found` |
| `R-INVOICE-BENEFICIARY` (added) | pay_invoice | payee differs from the stored beneficiary | DENY | `beneficiary_differs` |
| `R-INVOICE-BENEFICIARY` (amended P3-1, ADR 0014) | pay_invoice | amount is zero, in another currency, or more than is still owed (an instalment of up to the outstanding amount is allowed) | DENY | `invoice_amount_differs` |
| `R-RISK` (P3-1, ADR 0016) | payments, convert, schedule, add_payee | deterministic risk score at or above 6000 (MFA) or 9000 (DENY); reasons new_payee, untrusted_payee, held_payee, document_derived, amount_unusual, amount_extreme, unusual_hour, recent_denials, cop_mismatch | REQUIRE_MFA or DENY | `risk_high`, `risk_critical` |
| `R-PASSPORT` (P3-1) | issue_passport | no payment history | DENY | `no_payment_history` |
| `R-PASSPORT` (P3-1) | share_passport | passport not the user's, expired, claim not held, no audience | DENY | `passport_not_found`, `passport_expired`, `claim_not_held`, `audience_missing` |
| `R-TIER-HIGH` (P3-1) | share_passport | personal data leaves the user's control | REQUIRE_MFA | `shares_personal_data` |
| `R-CONTRIBUTION` (P3-1) | request_contribution | no family link, amount over the link limit, or not in the contributor's currency | DENY | `no_family_link`, `contribution_over_limit`, `contribution_currency` |
| `R-INVOICE-BENEFICIARY` (added) | pay_invoice | amount or currency differs from the stored invoice | DENY | `invoice_amount_differs` |
| `R-UNKNOWN` (added) | anything else | action type not known | DENY | `unknown_action` |

Rules marked "added" go beyond the table in docs/spec/modules.md 5.7: they make ownership and the invoice beneficiary
checks (which the P1 executor did ad hoc) part of the single deterministic decision.

## Illustrative values (`backend/config/policy.yaml`, assumptions, see `docs/assumptions.md`)

| Value | Setting |
|---|---|
| Low tier | NPR 25,000.00 equivalent, only for verified payees older than 30 days |
| Daily outgoing limit | NPR 1,000,000.00 equivalent in any rolling 24 hours |
| New payee age | 30 days: a payee added more recently never gets the low tier (always MFA) |
| Velocity | window 10 min, MFA above 3, DENY above 6 |
| Risk thresholds | high 6000 bps, critical 9000 bps (placeholders until P3) |
| Safety lock | 3 denied or failed high risk attempts, or 5 failed TOTP codes, in 10 min; 1 critical event |

## Combination

`combine()` in `corridor.contracts.policy` is a maximum over a total order, so it is associative,
commutative and idempotent. `evaluate(..., extra_rules=...)` exists so a property test can prove that adding
a rule never makes a decision less restrictive.

## Versioning

`PolicyConfig` is parsed from the YAML, its canonical JSON (RFC 8785 style) is hashed with SHA-256, and the
hash is `policy_version_hash` in every decision and ledger entry. Changing any value changes the hash.

## What happens after the decision

| Decision | Agent behaviour |
|---|---|
| DENY | no confirmation card, the reasons are explained, the denial is ledgered, high risk denials count toward the safety lock |
| REQUIRE_CONFIRM | card, then a CONFIRM level grant |
| REQUIRE_MFA | card, then a TOTP code is required to mint an MFA level grant |

The executor evaluates the policy again inside its transaction, so the decision in force at execution time
is the one that counts (time of check equals time of use).


## R-PAYEE-COOLOFF (P2-4): the control for a deceived user

Grants and MFA prove that the user pressed confirm and holds the phone. They cannot tell a user who knows
what they are doing from a user a scammer has convinced. This rule is the one control that still works when
the victim confirms and types a valid code. A destination account first saved less than 24 hours ago can
receive at most NPR 5,000 equivalent in total; above that the decision is DENY, with a scam warning and the
earliest time the payment is allowed. The values are illustrative assumptions (docs/assumptions.md).

- The age and the running total are kept per destination account (the hash of the account number), over
  every payee row with that hash. Renaming a payee, saving the same account again, or splitting a payment
  into several smaller ones does not reset or multiply the cap.
- It counts as a high risk denial for the safety lock (INV-11).
- Denying is not blocking forever: the same payment is allowed 24 hours after the account was first saved
  (still with the confirmation and MFA the other rules ask for).
- An invoice payment to anything other than the stored beneficiary stays DENY (`R-INVOICE-BENEFICIARY`),
  whatever MFA was given; the cooling-off does not replace it.
- Tests: unit tests per branch, Hypothesis properties (a young account never receives more than the cap,
  whatever the provenance, risk or action; splitting never exceeds the cap in total; extra rows for the same
  account never loosen the decision) and executor tests with a valid MFA grant.
