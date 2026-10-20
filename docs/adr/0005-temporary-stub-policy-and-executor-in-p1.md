# 5. Temporary stub policy and executor in P1, replaced in P2

## Status

Accepted, 2026-10-10. **Superseded on 2026-10-11 (P2-1)** by ADR 0006 (policy) and the real executor, grants and ledger (ADR 0007). The stubs and their tests were deleted.

## Context

Phase P1 needs the agent to complete real tasks end to end (propose, confirm, execute) so the task suite
and the dev chat can run. The real policy engine, taint propagation, grants, ledger and grounding
verifier are P2 work (docs/spec/modules.md 5.7 to 5.10, 5.14). Shipping the agent without any gate would break
INV-1 and INV-7 even temporarily, so P1 uses deliberately small stand-ins that are safe by construction
and obviously temporary.

## Decision

1. **`corridor.policy.stub.evaluate_stub`** returns `REQUIRE_CONFIRM` for `TRANSFER` and `PAY_INVOICE`
   (rule `STUB-CONFIRM-ALL`) and `DENY` for everything else (`STUB-DENY-UNKNOWN`, INV-10). It never
   returns `ALLOW`, which a Hypothesis test asserts for every action type, provenance and amount. It
   carries `TODO-P2` markers and a fixed version hash. The real engine replaces it in P2 without changing
   the graph's `policy` node contract.
2. **`corridor.executor.stub.execute_stub`** is the only agent path that writes sandbox state (INV-1).
   One database transaction: confirmed hash must equal the proposal hash (INV-5), account, payee and
   invoice are looked up with the proposal's user id (ownership), a reused idempotency key is rejected
   (INV-6), the debit is an atomic `UPDATE ... WHERE balance_minor >= :amount`, the transfer row is
   written, an invoice is marked paid. Any failure rolls back (INV-10).
3. **The graph pauses with a LangGraph `interrupt`** before execution. The confirm request carries the
   canonical proposal and its hash. Resume only works with the same hash. A missing or different hash is
   a `rejected` event and nothing moves. A new chat message or `cancel` discards the pending proposal.
4. **Trusted code, not the model, builds the proposal.** The planner returns read steps and at most one
   `ProposalDraft` of references. The amount comes from user typed evidence in the NLU frame, the payee
   from deterministic resolution against `get_payees` for the authenticated user, the source account from
   the currency, the idempotency key from code. Provenance is set by code only (INV-4).
5. **Routing is a temporary default.** One configured provider per corridor
   (`backend/config/corridors.yaml`, `default_providers`), marked `default_unoptimized`. The CP-SAT
   optimizer replaces it in P3.

## What P2 replaces

| P1 stand-in | P2 replacement |
|---|---|
| `evaluate_stub` | `corridor.policy` rules R-TIER-* and the rest of 5.7, most restrictive wins |
| hash compare in `execute_stub` | `ConfirmationGrant` (single use, 120 s), TOTP, safety lock |
| `execute_stub` | `corridor.executor`: fresh policy run, ledger append in the same transaction (INV-8) |
| template receipt, `ground` no-op | grounding verifier (INV-9) |
| all payee values `SYSTEM` | taint propagation, quarantined reader, `R-TAINT-PAYEE` |

## Contract change

`PlannerOutput` gained `proposal: ProposalDraft | None` and a new `ComposerOutput(reply)` was added to
`contracts/llm_outputs.py` (both are model output shapes with no provenance fields, as ADR 0002 requires).

## Consequences

The P1 demo cannot be used as a security claim. No document, image or message reaches any model
(attachments answer "document reading is not available yet"), and all outgoing actions need an explicit
confirmation, so the temporary behaviour is more restrictive than the final policy for ordinary use.

## Review follow ups (P1 security review, PASS WITH NOTES, all fixed in P1)

- A currency, payee or amount the parser supplied without evidence is asked again, never used (INV-4).
- One lock per thread; confirm and cancel re-check "awaiting" inside the lock, so they cannot interleave.
- Runs are no longer cancelled on client disconnect; any failure after the debit still reports "executed"
  with a fixed receipt; a duplicate idempotency key reply says the payment may already have been made.
- `/dev/chat` also requires a loopback `Host` header (DNS rebinding) and sends CSP and frame denial headers.
- Planner context carries no stored free text (no payee display name, no invoice issuer).
- Still for P2: executor check that route legs sum to the amount and payee currency matches the route,
  idempotency lookup that returns the earlier result, provenance column for document derived text (5.1).
