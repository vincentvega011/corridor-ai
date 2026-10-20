# Architecture

## Overview

Corridor AI is a safety constrained agent for cross border student finance. Models interpret and plan;
deterministic code decides and executes. This page records what exists now (end of phase P1) and what
each later phase adds. See docs/spec/architecture.md section 4 for the target design.

## Request lifecycle

One user turn through `corridor.agent.graph` (LangGraph `StateGraph`, checkpointed). Each node emits
`AgentEvent`s with a monotonic `seq` per trace id.

```
START
  |
ingest ---------------- split off pasted third party text (reader/pasted.py); attachments or pasted text?
  |                      -> read_untrusted: quarantined reader (local VL), documents stored UNTRUSTED,
  |                         typed summaries only leave this node (INV-3)
  |
parse         NLU cascade: deterministic normaliser + local Qwen3 parser, cloud fallback (intent event)
  |
clarify       one question at a time (missing slot, ambiguous or model-guessed value, unknown payee, past deadline)
  |  \------- question asked -> emit (turn ends, next message continues the pending request)
plan          read intents: deterministic read plan. Payments: planner (role "planner") returns
  |           PlannerOutput = READ steps + at most one ProposalDraft; validate_plan checks it
run_read_tools  read only tools in READ ONLY transactions scoped to the authenticated user
  |
specialists   default route for the corridor (P3: CP-SAT optimizer), conditions evaluated by code
  |
propose       trusted code builds the ActionProposal (provenance set by code) and its canonical hash
  |
policy        corridor.policy.evaluate on a fresh AccountState; PROPOSAL + POLICY_DECISION ledgered first;
  |           denial -> rejected (high risk denials count toward the safety lock)
request_confirmation  confirm_request card: exact fields, hash, required_level, reasons in the user's language
  |
await_confirmation    LangGraph interrupt. Before it resumes, AgentService.confirm checks the hash, the
  |                   safety lock and (level MFA) the TOTP code, and mints a single use grant for that hash
execute       corridor.executor: ONE transaction = idempotency, fresh policy, grant consumed, debit,
  |           transfer row, EXECUTION ledger entry. Failure -> rollback + EXECUTION_FAILED entry
compose       composer role writes the reply from tool facts; templates are the fallback
  |
ground        grounding verifier (INV-9): pass | regenerate once | template from facts; grounding event
  |
emit  ->  END
```

Interrupt and resume: `request_confirmation` and `await_confirmation` are separate nodes because a node
containing `interrupt()` re-runs from its first line on resume. A wrong TOTP code does not resume the graph:
the proposal stays pending and the user can retry (five wrong codes lock). A hash that does not match the
stored proposal is a `rejected` event with reason `hash_mismatch` and nothing executes. Checkpointer:
Postgres (`AsyncPostgresSaver`) for dev and demo, in memory or SQLite in tests.

Every node is wrapped by `_guarded`: any exception except LangGraph's own interrupt signal ends the turn
with an `error` event and a safe message (INV-10). A ledger failure raises and therefore fails the node.

## The safety core (phase P2-1)

```
 ActionProposal --> policy.evaluate(AccountState, PolicyConfig, now) --> ALLOW(read only) | CONFIRM | MFA | DENY
                                  ^ pure, no I/O (INV-2)                         |
 sandbox rows --> build_account_state                                            v
                                                          confirm: lock check, TOTP, grant (hash, user, 120 s, single use)
                                                                                 |
 executor: lock key, fresh policy, consume grant, debit, transfer, ledger  <-----+     all in one transaction
```

- `corridor.taint`: provenance helpers; trust only goes down. `USER_CONFIRMED` is created only in
  `corridor.confirm.upgrade` and only for a proposal whose grant was consumed (test scans the source).
- `corridor.policy`: rules, config, state; see `docs/policy-rules.md` and ADR 0006.
- `corridor.confirm`: grants (atomic single use), TOTP (replay protected), safety lock (persisted),
  `ConfirmationService`.
- `corridor.ledger`: hash-chained ledger, Merkle roots, verifier; ADR 0007.
- `corridor.executor`: the only writer of sandbox state.

## Modules

| Module | State at end of P2-1 |
|---|---|
| `corridor.sandbox` | schema, seeds, 9 read only tools, vault, `account_state` builder, provenance columns |
| `corridor.llm` | gateway: providers, rate limiter, failover, record and replay, prompt guard |
| `corridor.nlu` | normaliser, parser cascade, clarification templates, festival calendar |
| `corridor.agent` | graph, service, payee resolution, plan validation, proposals, routing, composition, reasons |
| `corridor.taint`, `policy`, `confirm`, `ledger`, `executor` | real, tested (P2-1) |
| `corridor.api` | `/chat` (SSE), confirm (with `totp`), cancel, `/unlock`, `/accounts`, `/payees`, `/transfers`, `/ledger`, `/ledger/verify`, `/ledger/proof/{seq}`, `/attachments`, `/dev/chat`, `/dev/totp` (dev only) |
| `corridor.reader` | upload sanitising, reader model call, injection signals, `documents` table, typed summaries, pasted-content split (P2-2) |
| `corridor.grounding` | registry, number extraction, verifier (P2-2) |
| `corridor.optimizer`, `risk`, `passport` | not built yet |

API trust boundary: the user id comes only from the bearer token (three fixed demo tokens in `secrets/`).
Requests are rate limited, body size limited and CORS is restricted to the frontend origin. A thread owned
by another user answers 404. `/dev/*` answers 404 unless APP_ENV=dev, the client is loopback and the Host
header is loopback.

## Runtime modes

`CORRIDOR_MODE=full` is what the API serves. The naive `baseline` (package `corridor.baseline`, docs/baseline.md)
runs on its own database and is reachable only through the evaluation runner and `POST /compare` (dev and demo).
The six ablation flags (`corridor.ablation`, ADR 0010) remove one defence each and are allowed only when
`APP_ENV` is `test` or `eval`, on an evaluation database; the API refuses to start with a flag set under dev or demo.

## Evaluation harness (P2-3)

`eval/run.py` runs the task suite and the red-team suite (`eval/redteam/*.yaml`, schema in `eval/cases.py`)
in any mode (`full`, `baseline`, each ablation, or `every`). Each case gets a freshly seeded database; the
simulated user types the turns, attaches fixtures from `data/fixtures` and confirms (with TOTP) or does something
else (wrong hash, twice). `eval/judge.py` decides success from database state, the event stream and replies, never
from an LLM. `eval/adapters.py` holds the replay-first, paced gateway. `eval/report.py` writes
`eval/reports/<timestamp>/report.json`, `report.md`, SVG and PNG charts, and points `latest.json` at the newest.
`make demo-attack` runs the main demo scenario (Priya, poisoned tuition invoice) on both agents.
