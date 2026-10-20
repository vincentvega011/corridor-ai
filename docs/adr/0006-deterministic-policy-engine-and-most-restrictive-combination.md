# 6. Deterministic policy engine and most-restrictive combination

## Status

Accepted, 2026-10-11. Supersedes the temporary policy stub of ADR 0005.

## Context

The product claim is that the model can be fooled and the money still cannot move. That needs one place
that decides what a proposed action requires, that no model can influence, and whose behaviour can be
proved with tests rather than argued.

## Decision

1. **`corridor.policy.evaluate(proposal, state, config, now)` is a pure function** (INV-2). It imports only
   the standard library, pydantic, yaml (the loader) and `corridor.contracts`. A test parses the source and
   fails on any other import, on any clock, file or random call in `rules.py`, `engine.py` and `state.py`.
2. **State is a value.** `AccountState` (balances, payees with age and stored provenance, open invoices,
   today's outgoing totals, recent transfer times, lock flag, the FX snapshot, flagged documents, corridors)
   is built by `corridor.sandbox.account_state` from database rows and passed in. The engine never queries.
3. **Rules are small independent functions** that each return zero or more `(rule id, decision, reason key)`.
   The decision is the maximum of all of them (INV-7), computed by `combine`, which is associative,
   commutative and idempotent. A rule cannot see another rule's result, so none can relax another.
4. **An action is never allowed on its own.** `R-FLOOR` returns `REQUIRE_CONFIRM` for every
   `ActionProposal`. `ALLOW` exists only for registered read tools (`evaluate_read`). Unknown actions deny.
5. **Provenance is read from stored state.** For payees and invoices policy uses the lowest of the
   proposal's provenance and the database column, so taint cannot be dropped by the proposal. A
   `MODEL_INFERRED` amount, payee or account is never treated as confirmed: inferred amounts need MFA.
6. **Limits are configuration**, expressed in NPR equivalents and converted with the FX snapshot, rounded up.
   `config/policy.yaml` is parsed, canonicalised and hashed; the hash is in every decision and ledger entry.
7. **Reasons are keys.** Rules return keys such as `R-TAINT-PAYEE.tainted_payee`; the user text comes from
   `data/lang/<code>/policy_reasons.yaml` (en, ne, romanised ne) with no model involved.
8. **The executor runs the policy again** inside its transaction (time of check equals time of use).
9. Rules beyond docs/spec/modules.md 5.7: `R-FLOOR`, `R-OWNERSHIP`, `R-INVOICE-BENEFICIARY`, `R-UNKNOWN`.

## Execution time behaviour (executor, same phase)

- The executor takes three locks in a fixed order: the idempotency key, then **the user** (so the daily limit
  and velocity, which sum over all of a user's accounts, are decided one payment at a time), then the source
  account row, then the ledger lock. A test with two accounts and parallel payments shows only one passes.
- A reused idempotency key returns the earlier result only for the same user, the same exact proposal hash
  **and the grant that was spent on that execution** (crash recovery after the commit). Anything else is
  `idempotency_key_reuse`. This deviates from the literal 5.9 text ("reject a reused key") on purpose: with
  the rule "return the earlier result only to the holder of the spent grant", a client that lost the reply
  can retry safely, and an attacker who only repeats a key gains nothing.
- The daily total is a rolling 24 hours, not a calendar day, so spending just before and after midnight does
  not double the cap.
- An unknown earlier amount (no FX rate) denies rather than skipping the daily limit.

## Safety lock details

Counted toward the lock: policy denials by attack related rules (`R-TAINT-PAYEE`, `R-INVOICE-BENEFICIARY`,
`R-INJECTION`, `R-RISK`, `R-VELOCITY`, `R-PAYEE-ADD`, `R-OWNERSHIP`, `R-SETTINGS`), grant failures, and a
confirmation for the wrong hash. Ordinary denials (balance, limits) do not count. Wrong TOTP codes count even
while locked; after five wrong codes in the window unlocking is refused for the rest of the window
(`unlock_cooldown`), so a locked account cannot be brute forced. The lock is per user, so an attacker who can
make a user's agent propose attacks (poisoned documents) can lock that user's outgoing payments; unlocking
needs the user's authenticator code, which limits the harm to an inconvenience.

## Verification

Branch coverage of `policy/` is measured with `pytest --cov=corridor.policy --cov-branch`. Hypothesis
properties in `tests/properties/test_policy_invariants.py`: (a) above the low limit never ALLOW or confirm
only, (b) a tainted payee never ALLOW or confirm alone, (c) adding a rule never lowers the decision,
(d) a locked session denies outgoing actions, (e) determinism, (f) no action is ever ALLOW.

## Consequences

Anything that needs a number the engine does not have (a new limit, a new risk feature) first has to enter
`AccountState` or `PolicyConfig`, which keeps the decision auditable. The P3 risk model feeds `R-RISK`
through `proposal.risk` only.

## Amendment (P2-3): lockout abuse by malicious documents

A denial caused by a document counts toward the safety lock at most once per document hash (`doc_denied` events carry the hash in `ref`), and all document caused denials together contribute at most one attempt to the count. Only the user's own repeated attempts (`denied`, `failed_high_risk`, wrong TOTP) can reach three. Three poisoned uploads, identical or different, cannot lock a user who does nothing else. A critical risk event still locks at once.

Known trade-off (security review, P2-3): the cap keys off a proposal having `derived_from_docs`, not off which rule denied it. A session that attaches a document to every induced transfer therefore counts those denials as at most one attempt, so the three-attempt lock does not fire and policy probing is not slowed by the lock. No money can move through this: every probe is still denied by the same rules, each one is ledgered, and a critical risk event still locks at once. We accept it to protect users from lockout by strangers (a poisoned document sent to the user), and list it in the threat model as residual risk. A possible later refinement is to cap only denials by document specific rules (`R-INJECTION`, document taint) and to count all other denials normally.

## Amendment (P2-4): the deceived user and `R-PAYEE-COOLOFF`

Context. Until P2-3 the evaluation assumed the user either confirms what they asked for or, at worst, taps
confirm on a card. The strongest realistic threat is different: a user who believes the attacker confirms
everything and types a valid authenticator code. WYSIWYS grants (INV-5) and MFA are then satisfied honestly,
and the other rules (taint, injection, invoice beneficiary) are the only barrier. Money to a brand new account
had no barrier of its own.

Decision. Add `R-PAYEE-COOLOFF` (docs/policy-rules.md). A destination account first saved less than 24 hours
ago can receive at most NPR 5,000 equivalent in total, otherwise DENY with a scam warning and the earliest
allowed time. The age and the total are per destination account (hash), so renaming, saving the account again
or splitting a payment does not help. It is a high risk rule for the safety lock. `R-PAYEE-ADD` changes: an
untrusted new payee is no longer an outright DENY but needs MFA and shows a scam education warning, because the
cooling-off now limits what the new account can receive and a real user may legitimately save an account
given in a message after checking it by another channel. `R-INVOICE-BENEFICIARY` is unchanged: an invoice is
paid only to its stored beneficiary and amount, whatever the grant or MFA.

Consequences. The deceived user can still be talked into small payments (up to the cap) and, after 24
hours, into larger ones; those need the other controls (the risk model in P3, human review for a bank). The
cap is an assumption, not a legal limit. A new ablation `ABL_NO_COOLOFF` removes only this rule so the
evaluation can show its contribution. No code path for ADD_PAYEE execution exists yet (the executor supports
transfers and invoice payments only), so the full agent cannot create a payee at all today; the rule protects
payees that exist (saved elsewhere, by a future settings screen or by the agent after P3).

## Amendment (P2-5): an account of untrusted origin is held, not capped

Context. P2-4 capped what a young account could receive (NPR 5,000.00 in total for 24 hours) whatever its origin.
The one unauthorized execution left in the full agent (RT-069) was a payment under that cap to an account taken from
untrusted data. A cap makes sense for a payee the user typed themselves (a real first payment is small and
legitimate). It makes less sense for an account that arrived in a document, a message or a model guess: those are the
accounts scammers supply, and the user cannot have chosen them knowingly.

Decision. `R-PAYEE-COOLOFF` has two caps. A destination account where any stored payee row with that account hash has
provenance `UNTRUSTED` or `MODEL_INFERRED` uses `payee_cooloff.untrusted_origin_cap_npr_minor`, which is 0 in the
shipped config: a full hold, nothing can be paid to it for 24 hours after it was first saved. An account the user
typed keeps `first_payment_cap_npr_minor` (NPR 5,000.00). One untrusted row taints the whole account, so saving the
same account again under a typed name does not launder it. The reason key is still `first_payment_cap_untrusted`
(`@<earliest time>|0`); its text now says the account is on hold. The rule reads provenance from stored state only
(INV-4, INV-8), never from the request.

Consequences. The residual loss of a deceived user to an untrusted-origin account is now zero inside the 24 hours, and
at most the typed cap for an account they typed. `ABL_NO_COOLOFF` raises both caps. `ABL_NO_TAINT` now also rewrites
the provenance stored on payees and invoices as the policy sees it, so the ablation really removes taint (it used to
strip only the proposal). The report adds a "bounded loss" metric: the NPR that reached accounts outside the stored
payees. Property tests: `tests/properties/test_policy_invariants.py` (untrusted origin never receives anything while
young; a typed account never exceeds the cap in total).

## Amendment (P2-7): after the hold

A hold that simply ended at 24 hours would let time launder an untrusted account into a fully trusted one, which
contradicts INV-4 (taint is sticky until the user confirms the exact value; time is not a confirmation). Decision:
after the 24 hour window, `R-PAYEE-COOLOFF` still applies to an account where any row has untrusted or model-inferred
provenance and no row is verified. Each payment then needs MFA with the scam warning repeated (`unverified_warning`),
and the total received in any rolling 24 hours is capped at `payee_cooloff.first_payment_cap_npr_minor` (NPR 5,000);
above it the decision is DENY (`unverified_cap`). Verification (a settings action, not an agent action) ends this.
A verified row or an account the user typed is unchanged. Before this change a payment above NPR 5,000 to such an
account was allowed (with MFA) 24 hours after it was saved.


## Amendment (P3-1): the deterministic risk score and the lock

`R-RISK` now scores every payment by deterministic rules (ADR 0016), and the score can reach critical (9000) on its own,
for example an unverified payee that does not match the bank's register. The earlier sentence "a critical risk event
still locks at once" now reads: **a critical score on a proposal the user typed locks at once (INV-11); a critical score
on a proposal built from documents counts like any other document caused denial** (once per document hash, at most one
attempt in total), so an attacker cannot lock a victim by sending poisoned documents, which a critical score would
otherwise allow for free. `confirm/lock.py` implements this (`record_policy_denial`) and
`tests/integration/confirm/test_confirm.py` pins it. Known trade-off, same as P2-3: a session that attaches a document to
every induced transfer is slowed by the lock less than one that does not; the denial itself, the cap and the MFA gate
still hold.
