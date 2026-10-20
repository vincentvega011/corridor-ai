# 10. Fair baseline and ablation methodology

## Status

Accepted, 2026-10-11.

## Context

The security story is a comparison: what a naive agent does under attack against what Corridor AI does, and
which defence is responsible for each stopped attack. A weak or rigged baseline would make the numbers
meaningless to the judges (bank risk people and professors), and ablation code that can run anywhere would
become an attack surface in the product itself.

## Decision

**Baseline** (`corridor.baseline`, docs/baseline.md). Same planner model, same READ tools, same reader model and
a sensible system prompt (known payees only, be careful, confirm large payments in chat, ignore instructions in
documents). It differs by having two direct write tools the model calls itself (`transfer`, `add_payee`), by
receiving the reader's whole output as text, and by lacking taint, policy, grants, MFA, idempotency, lock,
grounding and the ledger. It still asks "confirm?" in chat and a "yes" is its only approval. The INV-12 prompt
guard stays on for both modes; account numbers are shown to the baseline as aliases that its tools resolve.

**Isolation of the baseline.**
1. Own database (`corridor_baseline`; `corridor_eval_baseline` in evaluation), seeded like the sandbox.
2. `assert_isolated` at construction: refuse if the URL equals the sandbox URL or the name lacks `baseline`.
3. Own log (`logs/baseline.jsonl`, every line marked `BASELINE`), never the ledger.
4. Import boundary: only `corridor/api/compare.py` (and the evaluation runner) import it; a test walks the
   source tree. The baseline imports none of the defences or the executor.
5. Reachable in a running server only through `POST /compare`, which exists only when `APP_ENV` is dev or demo.
   `/chat` always runs the full agent and the API refuses to start with `CORRIDOR_MODE=baseline`.

**Ablations** (`corridor.ablation`). Six flags (`ABL_NO_QUARANTINE`, `ABL_NO_TAINT`, `ABL_NO_POLICY`,
`ABL_NO_GRANTS`, `ABL_NO_GROUNDING`, `ABL_NO_RISK`), each removing exactly one defence from the full agent:

| Flag | Removes | Does not remove |
|---|---|---|
| NO_QUARANTINE | split of pasted text from the user's words, and the typed-summary only planner context: third party text reaches the parser and the planner | taint, policy, grants, grounding (the reader still runs and flags documents) |
| NO_TAINT | provenance: values are rewritten to `system` before policy runs and at execution | policy rules on amount, payee age, injection flags, risk |
| NO_POLICY | the rule engine: replaced by its floor (`REQUIRE_CONFIRM`, rule `ABL-NO-POLICY`) | the confirmation itself, grants, MFA code check when asked |
| NO_GRANTS | hash binding: "confirm" executes whatever is pending and the executor needs no grant | idempotency, policy, the ledger |
| NO_GROUNDING | the numeric check of replies | everything else |
| NO_RISK | the risk score (a no-op until P3, kept so reports are stable) | everything else |

Fences: `Ablations` validates itself on construction (any flag needs `APP_ENV` in test or eval and an
evaluation or test database, never the sandbox name); the API resolves flags at startup and refuses to start
under dev or demo; the hooks sit in the callers (graph, executor, service), never inside `corridor.policy`,
`taint`, `confirm`, `ledger` or `grounding`, so the safety packages stay pure and their property tests are
unchanged. A test per flag shows that it changes its own behaviour and not another flag's.

**Runner.** Ground truth is database state, the event stream and the replies; no LLM judge. A fresh seeded
database per case. A request that was recorded before is served from its cassette, so repeated modes and seeds
cost nothing; live calls are recorded and paced under the Token Harbor limits. Cloud roles are forced to the
cloud provider so a failure never starts a local model.

## Consequences

- The comparison can be audited: prompt, tools and isolation are documented and tested.
- Ablations remove one defence at a time. Layers overlap, so removing one often changes little; that is
  defence in depth and the report says so rather than hiding it.
- The baseline is one design. Its results are reported with its prompt, and the report lists this as a limit.
- Because temperature is 0 and identical requests replay, extra seeds repeat the same model outputs; seed spread
  is not a confidence interval (stated in every report).

## Amendment (P2-4): a representative baseline, two user models, a seventh ablation

1. **The baseline reads documents like a typical agent.** Until P2-3 it received the output of our quarantined reader (typed fields, 120 character cap, flagged notable lines), which is part of the defence being measured. It now gets a full transcription from the same local vision model (`baseline/transcribe.py`): every visible line, including small and faint text, as ordinary content, no schema, no flags. Same planner, same read tools, the two direct write tools, account aliasing for the INV-12 guard, and every safety instruction unchanged.
2. **Two user models** (`eval/simulator.py`), reported separately and identical for both agents: `fooled_user` (carries out the scripted believing user, never refuses) and `cautious_user` (refuses at the card or chat question unless it matches the request). The simulator rewrites no scripted turn: an early version that forced confirmations changed case meanings and was corrected.
3. **`ABL_NO_COOLOFF`** removes only `R-PAYEE-COOLOFF` (a cap no payment can reach, same policy hash), so the report can show the contribution of the fooled-user control. The other six flags are unchanged.
4. **Targeted cases** carry a `decisive` field naming the one defence they test; the report has a table per defence. A case that never reaches its defence is a case defect to fix, and several were (pronouns, titles, past dates).
5. **Consequence.** The fair baseline resists most injection text with this planner model, so quarantine, taint and grounding show no ablation effect in this suite; policy, grants and the cooling-off do. We report that instead of weakening the baseline.
6. Ablated runs keep the normal policy version hash in the ledger (the rules differ, the config file does not); the eval report names the mode, so this is only an eval note. `payee_cooloff.hours` is limited to 24 because the running total is built from the last 24 hours of transfers (security review P2-4).
