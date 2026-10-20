<!-- Architecture and security invariants. Section numbers are kept so references resolve. -->

## 3. Security invariants (never break these)

These are the product. Each invariant has an ID; tests and documents reference these IDs. A change that would weaken one is rejected, and a change to `backend/src/corridor/policy/`, `taint/`, `executor/`, `ledger/` or `grounding/` requires the property tests in `tests/properties/` to pass, no regression in the red-team suite, and a note in `docs/adr/` if behaviour changed.

| ID | Invariant |
|---|---|
| **INV-1** | **The LLM never executes.** LLMs only produce `ActionProposal` objects. Only `corridor.executor` can change sandbox state, and only after `corridor.policy` returns an allowing decision. No tool callable by an LLM may mutate balances, payees, limits, credentials or settings |
| **INV-2** | **Policy is deterministic code.** `corridor.policy` imports no LLM client, makes no network calls, and is a pure function of `(ActionProposal, AccountState, PolicyConfig, Clock)`. Same input, same decision |
| **INV-3** | **Untrusted content never reaches the privileged planner as text.** Documents, images, SMS, emails, invoice text and any third-party content go only to the quarantined reader. The planner receives typed, schema-validated fields, never raw strings from untrusted sources |
| **INV-4** | **Taint is sticky.** Any value derived from untrusted content carries `Provenance.UNTRUSTED` until the user explicitly confirms it. Tainted payee identifiers or account numbers can never be used to create a payee or execute a transfer without strong verification (MFA). Taint cannot be removed by an LLM |
| **INV-5** | **What You See Is What You Sign (WYSIWYS).** A user confirmation creates a `ConfirmationGrant` bound to the SHA-256 hash of the exact canonical proposal (amount, currency, payee id, source account, route, idempotency key). The grant is single-use and expires after 120 seconds. If any field changes after confirmation, the grant is invalid |
| **INV-6** | **No duplicate payments.** Every executable action has an idempotency key. The executor rejects a second execution with the same key. Retries, double clicks and replayed messages can never pay twice |
| **INV-7** | **Most restrictive decision wins.** Policy rules each return a decision; the combined decision is the most restrictive (`DENY > REQUIRE_MFA > REQUIRE_CONFIRM > ALLOW`). No rule can relax another rule |
| **INV-8** | **Every decision is logged.** Every proposal, decision (with rule IDs and policy version hash), confirmation, MFA attempt, execution and rejection is appended to the hash-chained ledger before the response returns. Ledger writes failing means the action fails (fail closed) |
| **INV-9** | **Numbers must be grounded.** Any amount, rate, fee, balance or date shown to the user must trace to a tool result from the same turn. Ungrounded numbers are blocked by the grounding verifier |
| **INV-10** | **Fail closed.** Any exception, timeout, schema validation failure or unknown state in the safety path results in no execution and a safe message. Never default to allow |
| **INV-11** | **Safety lock.** Three denied or failed high-risk attempts within 10 minutes, or one critical-risk event, locks outgoing transfers for the session until MFA re-verification |
| **INV-12** | **Least data.** The planner receives only the account fields needed for the current task. Full account numbers are masked in LLM context (last 4 digits). Secrets, MFA seeds and full identifiers never enter any prompt |

## 4. Architecture

### 4.1 Component diagram

```
 Customer app (mobile-first PWA)            Bank operations console (desktop)
            │  REST + SSE                              │  REST + SSE
            ▼                                          ▼
 ┌────────────────────────────── FastAPI gateway ───────────────────────────────┐
 │ auth (demo users) · rate limit · request size limits · trace ids            │
 └──────────────┬───────────────────────────────────────────────┬──────────────┘
                ▼                                               ▼
 ┌──────────────────────────────┐                ┌──────────────────────────────┐
 │ 1. NLU cascade               │                │ Quarantined reader (local VL)│◄── untrusted docs,
 │ local fine-tuned Qwen3 parser│                │ no tools, typed output only  │    images, SMS
 │ → Qwen API fallback if low   │                │ every field Provenance.UNTRUSTED
 │ confidence                   │                └──────────────┬───────────────┘
 └──────────────┬───────────────┘                               │ typed fields
                ▼ IntentFrame                                   ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │ 2. Planner (Qwen API, privileged, LangGraph)                                 │
 │ builds a Plan DAG, calls READ-ONLY tools, emits ActionProposals              │
 │ never sees raw untrusted text (INV-3)                                        │
 └───────┬──────────────────────┬──────────────────────┬────────────────────────┘
         ▼                      ▼                      ▼
 ┌───────────────┐      ┌───────────────┐      ┌────────────────┐
 │ Routing       │      │ Risk / scam   │      │ Credit Passport│
 │ optimizer     │      │ model (LGBM + │      │ issuer (Ed25519│
 │ (OR-Tools)    │      │ SHAP)         │      │ VC)            │
 └───────┬───────┘      └───────┬───────┘      └────────┬───────┘
         └──────────────────────┼───────────────────────┘
                                ▼ ActionProposal (+ taint, risk score, route)
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │ 3. POLICY ENGINE (pure Python, no LLM, versioned + hashed)  INV-2, INV-7     │
 │ → ALLOW | REQUIRE_CONFIRM | REQUIRE_MFA | DENY  + rule ids + reasons         │
 └──────────────────────────────┬───────────────────────────────────────────────┘
                                ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │ 4. Confirmation & MFA (WYSIWYS grants, TOTP)                    INV-5        │
 └──────────────────────────────┬───────────────────────────────────────────────┘
                                ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │ 5. Executor (only writer of sandbox state, idempotent)    INV-1, INV-6       │
 └──────────────────────────────┬───────────────────────────────────────────────┘
                                ▼
 ┌──────────────────────────────┐   ┌──────────────────────────────────────────┐
 │ Mock bank sandbox (Postgres) │   │ Hash-chained audit ledger + Merkle roots │
 └──────────────────────────────┘   └──────────────────────────────────────────┘
                                ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │ 6. Response composer → Grounding verifier (INV-9) → user (in their language) │
 └──────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Request lifecycle (one turn)
1. Gateway authenticates the demo user, assigns `trace_id`, opens an SSE stream.
2. If the turn contains an attachment (image, PDF, pasted SMS), it is routed **only** to the quarantined reader, which returns an `ExtractedDocument` whose every field is `Tainted[..., UNTRUSTED]`.
3. NLU cascade parses the user's own words into an `IntentFrame` (language, intent, slots, confidence, missing slots). If confidence is below threshold, the Qwen API parser is used; the cascade decision is logged.
4. If slots are missing or ambiguous, the agent asks one clarifying question (interaction score).
5. Planner builds a `Plan` (DAG of `PlanStep`s). Plan is validated: acyclic, at most 12 steps, only registered tools, read-only tools before proposals.
6. Read-only tools run (balances, quotes, invoices, payees, history). Each result is registered in the turn's `ToolResultRegistry` (for grounding).
7. Specialists run: optimizer picks route, risk model scores, passport issuer if requested.
8. Planner emits `ActionProposal`s. Each is canonicalised and hashed.
9. Policy engine evaluates each proposal. Decision + rule ids + policy hash go to the ledger.
10. If `REQUIRE_CONFIRM` or `REQUIRE_MFA`, the graph **interrupts** (LangGraph interrupt) and the UI shows the exact proposal. User confirms (and enters TOTP if needed); a `ConfirmationGrant` is minted for that exact hash.
11. Executor verifies grant, idempotency key and fresh policy decision (re-evaluated at execution time, time-of-check equals time-of-use), then mutates the sandbox inside one DB transaction together with the ledger append.
12. Response composer writes the reply in the user's language; grounding verifier checks every number; reply streams to the user. Every step emits an `AgentEvent` to the console.

### 4.3 Two runtime modes (for evaluation and the demo)
- `CORRIDOR_MODE=full`: everything above.
- `CORRIDOR_MODE=baseline`: **naive agent**. Same planner model, same tools, same prompts, but untrusted content goes straight into the planner prompt, there is no taint tracking, no policy engine (proposals execute directly), no WYSIWYS grants, no grounding verifier. This is the comparison point for every security metric and the left side of the side-by-side demo.
- Ablation flags (each can be toggled independently in `full` mode): `ABL_NO_QUARANTINE`, `ABL_NO_TAINT`, `ABL_NO_POLICY`, `ABL_NO_GRANTS`, `ABL_NO_GROUNDING`, `ABL_NO_RISK`, `ABL_NO_COOLOFF` (added in P2-4 for the payee cooling-off rule). Ablations exist only for the eval runner and are refused when `APP_ENV=demo`.
