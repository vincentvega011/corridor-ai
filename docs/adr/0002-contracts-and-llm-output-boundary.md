# 2. Shared contracts and the LLM output boundary

## Status

Accepted, 2026-10-10.

## Context

Corridor AI claims that the model can be fooled but money cannot move. That claim fails if a model can
tell our code that a value is trusted, who the user is, or which idempotency key to use. Several
parallel development sessions also build against the same shapes, so they must be fixed early.

## Decision

1. All shared types live in `corridor.contracts`. Every model is Pydantic v2 with `frozen=True` and
   `extra="forbid"`. Money is integer minor units, rates are Decimals quantised to 8 places.
2. **LLM output boundary.** `contracts/llm_outputs.py` defines the only schemas a model may return
   (`ParserOutput`, `PlannerOutput`, `ProposalDraft`, `ReaderOutput`). They hold plain values and have
   no provenance, trust or source field. Because extra fields are forbidden, a model response that
   carries `provenance` is rejected. Provenance is attached by our code during conversion:
   parser values become USER_TYPED or MODEL_INFERRED, reader values become UNTRUSTED (and the document
   models re-force UNTRUSTED), tool values become SYSTEM. This supports INV-4 (a model cannot remove taint)
   and INV-3 (untrusted content arrives as typed, length capped fields).
3. `ProposalDraft` omits `user_id`, `idempotency_key`, `risk` and `route`. Trusted code supplies them, so a
   model cannot choose whose money moves, reuse a key, or invent a route (INV-1, INV-6).
4. **WYSIWYS hash.** `ActionProposal.canonical_hash` covers action, user, source account, payee, amount,
   invoice id, route legs and idempotency key, plus a schema version. It excludes provenance (it changes
   when the user confirms), proposal id, risk, source documents and route aggregates (derived from legs).
5. **Canonical JSON** follows RFC 8785 style with documented deviations (floats rejected, Decimals as
   strings, UTC datetimes with Z).
6. `Tainted` has no upgrade method. Upgrading to USER_CONFIRMED is reserved for `corridor.confirm`.
   `lowest_trust` treats USER_CONFIRMED as USER_TYPED when deriving, so confirmation never propagates.
7. `Decision.combine` is a maximum over a total order (INV-7); an empty input yields DENY (INV-10).

8. **Review follow-ups** (security-reviewer pass 1 blocked on three items, all fixed):
   - `ActionProposal` now carries typed terms for every action (`new_payee`, `schedule`, `schedule_id`,
     `budget`, `target_currency`) and hashes them, so no confirmed term lives outside the hash.
     `HASH_SCHEMA_VERSION` is 2. The amount must equal the sum of route legs; route aggregates are validated.
   - The ledger entry hash also covers `trace_id`, `actor` and `event_type` (a deliberate deviation from the
     docs/spec/modules.md 5.10 formula) so tampering with those columns is detected. `payload_hash_of` is the single
     payload hash helper.
   - `ProposalDraft` cannot carry an amount or payee together with `invoice_ref`; invoice payments take both
     from the stored invoice with its UNTRUSTED provenance. LLM facing amounts are positive and bounded.

## Consequences

- Python cannot prevent trusted code from constructing a `Tainted` with any provenance. The guarantee is
  structural (no LLM facing schema carries provenance) plus review and tests at the few construction sites.
- Sequence fields are tuples for immutability. Dict fields (`slots`, `args`, `data`) remain shallowly
  mutable; consumers must not mutate them.
- Changing a hashed field of `ActionProposal` invalidates outstanding grants; bump `HASH_SCHEMA_VERSION`.
- Floats (risk score, confidence) cannot be canonicalised; ledger payloads must use explicit DTOs.
- Code outside `corridor.confirm` must not use `model_construct`, `model_copy(update=...)` on `Tainted`
  values, or `Provenance.USER_CONFIRMED`; a repository test should enforce this when `confirm` is built.
