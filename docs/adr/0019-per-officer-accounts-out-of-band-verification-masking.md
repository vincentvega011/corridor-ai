# 0019. Per-officer accounts, out-of-band verification, account number masking, demo-only features

Date: 2026-10-12. Status: accepted (P3-3 Part 0).

## Decision
1. **INV-12, defence in depth.** Before the user's words reach any model, `llm.guard.mask_account_numbers` replaces
   account-like numbers with `[ACCOUNT]`: any run of 6 or more digits, a BSB (`123-456`), and digit groups joined by
   single spaces or hyphens that together reach 6 digits (card length numbers included; Devanagari digits are
   normalised first). ISO dates, UUIDs, hashes and comma grouped money next to a currency code are left alone. It is
   applied to the parser's "Message:" line (and so to the cloud parser fallback) and to the composer's
   `user_request`. The planner never receives free text. The deterministic hints no longer list a bare run of 6 or
   more digits as an amount. The typed payee origin check (`nlu/typed_payee.py`) reads the original text in code
   only. A plain amount of 6 or more digits is masked in the message but still reaches the model as a computed hint
   when it carries a currency. Tests: `tests/integration/agent/test_account_masking.py` (Australian BSB with account,
   Nepali 15 and 16 digit accounts, a CNY card number, a six digit number) assert on the prompts a gateway recorded.
2. **One credential per officer.** `secrets/officer_tokens.json` holds a credential for each synthetic officer
   (`officer_asha`, `officer_binod`). `officer_id_for` compares a bearer token with every credential in constant time
   and returns that officer's id, which is written to the ledger by every officer action. A customer token matches
   none (the test over every `/officer` route stays). `GET /officer/me` tells the console who logged in.
3. **Out-of-band verification.** `verify_payee` needs three things, all ledgered with the officer id: the Confirmation
   of Payee result (computed by the server, must be a match), a method from a fixed list
   (`callback_independent_number`, `in_branch_check`, `university_housing_office_list`) and a free text reference of
   at least 3 characters. Missing or unknown method or a short reference is refused (`method_required`,
   `reference_required`) and does not close the customer's request. The request body is `{method, reference}`
   (replaces `{reason}`). A CoP match is not proof: the officer's out-of-band check is the control (ADR 0018).
4. **Sandbox-only features are not in a production build.** The persona switcher and the code that reads the demo
   tokens (`@demo/persona-switcher`, `@demo/session`) are compiled in only when `NEXT_PUBLIC_DEMO_PERSONAS=1`;
   otherwise two build aliases point at inert stand-ins and the customer sign-in answers 401. `make web-check` builds
   without the flag and fails if the output contains the switcher, `demo_tokens.json` or the persona cookie.
   Backend dev routes (`/dev/*`, `/compare`) were already refused outside dev and demo, and `LLM_RECORD_ROLES` is
   refused outside dev, demo, test and eval.
5. **Contributor endpoints** `GET /contributions` and `POST /contributions/{id}/respond` (added in P3-2) are approved
   and part of the API: see `docs/spec/api.md` and the generated `frontend/lib/contracts.ts`. They record the answer
   and a ledger entry and move no money.

## Consequences
Eval and tests call `verify_payee(..., method, reference)`. `docs/spec/api.md` documents the verify body and
`/officer/me`. The console (P3-3) logs each officer in with their own credential.

## Addendum (P3-3 Parts 1 and 2: console data, observability only)
`PolicyDecision.rule_results` (rule id, the rule's own decision, reason key) is ledgered with each policy decision, the
parser result is ledgered as `cascade_decision` (intent, parser, confidence, slot names with their origin, never
values), and `GET /officer/proposals/{hash}/trace` returns every entry of the same turn. None of this changes a
decision: the combined decision is still the most restrictive rule (INV-7). The console reads results and numbers
only from the published report (`eval/reports/latest.json`) and shows its id, date and stamp.

## Known limits (security review of P3-3, PASS WITH NOTES)
- Masking now also joins digit groups with underscore, slash, dot and comma (fixed after the review). A currency
  tagged amount of 6 or more digits stays in the deterministic hints (it is money); the "Message:" line is masked.
- The production exclusion of the persona switcher is configured for Turbopack and for webpack; `make web-check`
  builds with the default bundler only.
- The officer's free text reference is ledgered verbatim (up to 200 characters) and never reaches a model.
- The console has no login rate limit (credentials are 24 random bytes; sandbox).

## Addendum (P3-4, revised in P3-4b): the parser's output is not in the ledger

The officer trace shows what the parser model said. The ledger (append only, hash chained, cannot forget) holds only
`parser_output_hash` in the `cascade_decision` entry: SHA-256 over the trace id and the masked text. The masked JSON
(`mask_account_numbers`, capped at 1500 characters, the user's own words as slots, never document text) is stored in
the `parser_traces` table (migration 0013, `corridor.sandbox.parser_trace`), outside the chain. **Retention: 7 days**,
then the row is deleted (pruned on every write) and the console shows "expired". The ledger and its chain are unaffected by
expiry. A production deployment would set the period by policy. Tests: `test_the_ledger_holds_only_a_hash_of_the_parser_output_and_the_text_expires`.

Limits: ledger entries written between P3-4 and P3-4b still carry the old `parser_output` field (masked) and cannot be erased
because the ledger is append only; the 7 day retention applies to entries written from P3-4b on. Pruning runs on writes, expired
rows are never returned. A customer route never reads `parser_traces`; the trace endpoint is officer only.

## Addendum (P4-1): the sandbox authenticator panel

`GET /demo/authenticator` returns the current code of the token's own persona so a recording can show where the six digit code
comes from (a stand-in for the user's phone). It is the one place a code is shown by the product, and it exists only in the
sandbox: APP_ENV dev or demo, loopback client, loopback Host header, no proxy header (the same checks as `/dev/totp`), 404
otherwise; the code and seed are never logged. The panel is compiled into the customer app only when `NEXT_PUBLIC_DEMO_PERSONAS=1`
(build alias plus the gateway allowlist) and `make web-check` fails if a production build contains it. Known limit: `APP_ENV`
defaults to dev, so a deployment must set it (a deployment note, not a code path in production).
