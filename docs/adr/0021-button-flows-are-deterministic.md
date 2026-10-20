# ADR 0021: button flows are deterministic, chat flows use the planner

Status: accepted (P4-2).

## Context
The Tuition "Start the plan" button sent a chat sentence, so a screen that already knew the invoice went through the parser and
the cloud planner. When the planner was unavailable the button failed, although every number (the CP-SAT funding plan, the
accounts, the payees) is computed by code. A model added nothing to the proposal: after the planner chooses its read tools, the
proposals are built by code from stored records.

## Decision
- `POST /chat` accepts an optional `action` (`StructuredAction`: `pay_invoice` with an invoice id, or `send_money` with a payee id,
  an amount and a currency, plus the screen language). When it is set, the graph builds the frame in code (parser `structured`,
  amounts and payee carry USER_TYPED provenance because the customer entered them in the form), skips the planner, and runs the
  same clarify, read, specialist, proposal, policy, grant, MFA and executor nodes as the chat path. Payees and invoices are looked
  up by id among the caller's own records; an id of another customer yields no card.
- The buttons that use it: Tuition "Start the plan", Payees "Pay", Routes "Send with the recommended route". Payee saving, the
  passport and the family answers already used their own endpoints and never needed a model.
- Chat keeps the parser and the planner for natural language. A test (`test_structured_flows.py`) checks that a button and the
  equivalent chat message produce identical proposals (every hashed field except the random idempotency key, the card text, the
  route and the policy outcome) and that the button path calls no model role at all.

## Consequences
INV-1 holds with no model involved in a button flow: nothing executes without the policy decision and the grant bound to the
proposal hash (INV-5), idempotency and the ledger are unchanged (INV-6, INV-8). The ledger records `parser: structured` in the
`cascade_decision` entry. With the cloud planner blocked, the three money buttons still work.

## Review fix
The graph checkpoint keeps every key of the input, so `structured` is written on every turn (`None` for a typed one); otherwise a button turn leaked into the next typed turn of the same thread and produced a stale card. Regression test: `test_a_button_turn_does_not_leak_into_the_next_typed_turn_of_the_same_thread`.

## Addendum: trace origin (no new field)
A button proposal already records its origin in the ledger: its `cascade_decision` entry has `parser: "structured"` and `reasons: ["button"]` (no parser output hash). The bank console trace reads that to show one "Button action (no model)" node in the deterministic zone instead of parser and planner nodes. No API or contract change was needed.
