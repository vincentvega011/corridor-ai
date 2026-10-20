# 7. Hash-chained ledger with a restricted database role and Merkle roots

## Status

Accepted, 2026-10-11.

## Context

Every proposal, decision, confirmation, MFA attempt, execution, rejection and lock event must be recorded
in a way that makes silent editing detectable, and recording must not be skippable: if the record fails the
action fails (INV-8). The ledger is hash-chained and tamper evident. It is not a blockchain.

## Decision

1. **Schema.** `ledger_entries(seq, ts, trace_id, actor, event_type, payload_canonical_json, payload_hash,
   policy_version_hash, prev_hash, entry_hash)` and `ledger_roots` (immutable Merkle root snapshots).
   `entry_hash` is the SHA-256 of the canonical JSON of `[prev_hash, seq, ts, trace_id, actor, event_type,
   payload_hash, policy_version_hash]` (ADR 0002). The genesis entry has `seq` 1 and an all zero `prev_hash`.
2. **Same transaction.** `Ledger.append` runs inside the caller's transaction, so an execution entry commits
   or rolls back with the debit and the transfer row. If the append fails, `LedgerError` fails the action.
3. **Restricted role.** Migration 0004 creates the `NOLOGIN` role `corridor_ledger` with `INSERT` and
   `SELECT` only on the two ledger tables, revokes everything from `PUBLIC`, and the append executes after
   `SET LOCAL ROLE corridor_ledger`. The application therefore writes the ledger with a role that cannot
   update, delete or truncate it. `SET LOCAL ROLE` was chosen over a second connection so the entry shares
   the transaction of the state change; a separate login role could not give that atomicity.
4. **Defence in depth.** Row triggers `BEFORE UPDATE OR DELETE` and statement triggers `BEFORE TRUNCATE` raise
   on both tables for every role, even the table owner. Changing history takes an explicit administrator
   action (disable the trigger), which is what `make tamper-demo` and the tamper tests do. In the dev setup
   the application connects as the table owner, which can still disable a trigger; a production deployment
   uses a separate owner role for migrations and gives the application login only membership in
   `corridor_ledger` (INSERT and SELECT).
5. **Serialised appends.** A transaction level Postgres advisory lock is taken before reading the last
   entry, so `seq` is contiguous under concurrency. The lock is held until commit and is taken last in the
   executor, so it cannot deadlock with row locks.
6. **Verifier.** `verify_chain` recomputes everything: genesis, contiguous `seq`, `prev_hash`, `payload_hash`
   from the stored text, canonical form of that text, `entry_hash`, timezone aware timestamps, and every
   stored Merkle root. Any exception means "broken", never a crash. It reports the first broken seq.
7. **Merkle roots.** RFC 6962 style trees (leaf and node prefixes, no duplicated leaf) over entry hashes,
   per UTC day and per session (the session id is in the hashed payload). `compute_roots` stores a snapshot
   when a root changes. Inclusion proofs are served by `GET /ledger/proof/{seq}`.
8. **API.** `GET /ledger` (the caller's own entries), `GET /ledger/verify` (global, content free) and
   `GET /ledger/proof/{seq}` (own entries only). An operator view comes with the console in P3.

9. **Anchoring.** The executor stores fresh Merkle root snapshots after every committed execution
   (best effort, in its own transaction), so deleting the newest entries is detected by the stored roots.

## Limits that are stated, not hidden

- The chain alone cannot show that the newest entries were deleted, or that someone with admin rights
  rewrote the whole chain consistently. Both are caught only once a Merkle root snapshot exists, and a
  production system would also anchor roots outside the database (signed and stored elsewhere).
- Payloads can contain user text only through fixed fields; no raw message content is ledgered.
- Reserved payload keys (`user_id`, `session_id`) are written last, so a caller cannot override them.
- Roots live in the same database. A real deployment would also sign and store them elsewhere.
- If recording a failure itself fails, that failure is not ledgered (the action still fails closed).
- Appends take the ledger advisory lock; the lock row of a locked user is taken in a different order by the
  policy node and the executor failure path. A deadlock would abort one transaction, which fails closed.
- `/ledger/verify` is rate limited (it recomputes the whole chain) and `/ledger/proof` returns sibling hashes
  of other users' entries (hashes only, no content).
