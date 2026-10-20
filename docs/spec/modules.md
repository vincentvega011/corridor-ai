<!-- Module specifications (section 5). Section numbers kept so old references still resolve. -->

## 5. Module specifications

Each module lists: location, responsibility, interface, acceptance criteria. "Done" means code + tests + docstring + entry in `docs/architecture.md`.

### 5.1 `corridor.sandbox`: mock bank
- **Location**: `backend/src/corridor/sandbox/`
- **Responsibility**: synthetic bank with multi-currency accounts, payees, providers, FX tables, invoices, schedules, transaction history, credit events.
- **Tables** (SQLAlchemy 2, Postgres; SQLite allowed in tests):
  - `users(id, display_name, home_region, locale, risk_profile, mfa_seed_ref, created_at)`
  - `accounts(id, user_id, currency, balance_minor, account_no_masked, account_no_hash, status)`
  - `payees(id, user_id, display_name, region, currency, provider_ref, account_no_hash, verified_at, added_via, created_at)`
  - `providers(id, name, corridor_from, corridor_to, fee_fixed_minor, fee_pct_bps, fx_margin_bps, min_minor, max_minor, eta_hours, active)` (names are fictional, for example "SwiftHome", "PeakRemit", "KathmanduLink", "BankWire")
  - `fx_mid_rates(pair, rate, as_of, source)`
  - `invoices(id, user_id, issuer, amount_minor, currency, due_date, beneficiary_payee_id, status, source_doc_hash)`
  - `transfers(id, user_id, from_account, payee_id, amount_minor, currency, route_json, status, idempotency_key UNIQUE, proposal_hash, created_at, executed_at)`
  - `schedules(id, user_id, proposal_template_json, rrule, next_run, status)`
  - `credit_events(id, user_id, kind, amount_minor, currency, due_date, paid_date, on_time)`
- **Money**: always integer minor units + ISO 4217 currency. Never floats for money. Use `decimal.Decimal` for FX rates, quantised to 8 places, rounding `ROUND_HALF_EVEN`.
- **Seeds**: `data/seeds/*.yaml` with 3 personas (Priya, Ramesh, Anil), 6 months of history, 4 providers per corridor, sample invoices (one clean, one poisoned).
- **Read tools** (exposed to planner, read-only): `get_balances`, `get_payees`, `get_fx_quote`, `list_providers`, `get_invoice`, `get_history`, `get_schedules`, `get_credit_summary`.
- **Acceptance**: `make seed` builds a fresh DB; read tools return Pydantic models; no read tool can mutate (enforced by a test that runs every read tool inside a read-only transaction).
- **Implemented (P2-2)**: provenance columns exist (migration 0004) and the read tools return them; uploaded documents are stored in `documents` (migration 0005).
- **Added acceptance criteria (security review)**:
  - Free text columns that can originate from a document (`payees.display_name`, `payees.added_via='document'`, `invoices.issuer`) carry no provenance yet. Before any P2 writer stores document derived text there, add a provenance column and have the read tools return it `Tainted` (or a fixed label), so a poisoned name never reaches the planner as trusted text (INV-3, INV-4).
  - The executor must enforce that `from_account`, `payee_id` and `invoice.beneficiary_payee_id` belong to the transfer's `user_id` (or add composite foreign keys), and that the transfer currency equals the account currency.
  - Prompt builders render money with `corridor.formatting`; tool `data` never contains a bare 9 digit integer.

### 5.2 `corridor.llm`: model gateway
- **Location**: `backend/src/corridor/llm/`
- **Responsibility**: one interface for all model calls, structured output, retries, timeouts, logging, recorded-replay.
- **Interface**: `async def complete(role: ModelRole, messages, schema: type[BaseModel] | None, tools: list[ToolSpec] | None) -> LLMResult`
- **Roles and default backends** (configured in `config/models.yaml`, never hardcoded):

| Role | Default provider and model | Backend |
|---|---|---|
| `planner` | `tokenharbor`, a DeepSeek V4.1 Flash `:free` model (env `PLANNER_MODEL`); falls back to `local_planner` | cloud |
| `parser` | Our LoRA fine-tuned small Qwen3 (1.7B or 4B, 4-bit) | local, `mlx_lm.server` (`local_mlx`) |
| `parser_fallback` | `deepseek-v4.1-flash:free` on `tokenharbor` (same open-weight model as the planner) | cloud |
| `reader` | Small Qwen-VL model (Qwen3-VL 4B, 4-bit) | local, Ollama (`local_ollama`) |
| `composer` | `deepseek-v4.1-flash:free` on `tokenharbor` | cloud |
| `local_planner` | Qwen3 4B 4-bit, started on demand when the cloud planner fails (7.4) | local, `mlx_lm.server` on 8082 |

Providers are first class in `models.yaml` (base URL env, key env, client-side limits): `tokenharbor` (enabled), `alibaba` (disabled, paid only), `local_mlx`, `local_ollama`. Local models: parser base `mlx-community/Qwen3-1.7B-4bit`, local_planner `mlx-community/Qwen3-4B-4bit`, reader Ollama `qwen3-vl:4b-instruct-q4_K_M`. The exact cloud model ids come from `GET /v1/models` and are set in `.env`, never in code. All three cloud roles use DeepSeek V4.1 Flash (MIT licensed open weights) to keep the stack open-weight; `claude-haiku-5.5:free` is used only in the eval comparison set as a cross-model-family check. Limits are per account, so using one model for all roles costs no extra quota.

- **Endpoints**: all backends speak the OpenAI chat-completions protocol (Token Harbor, Model Studio compatible mode, `mlx_lm.server`, Ollama `/v1`). Base URLs and model ids come from env. **Verify model ids against the provider's live `/v1/models`; do not guess model names in code.**
- **Determinism**: temperature 0 for parser, planner and reader in eval runs; seed set where supported.
- **Record/replay**: `LLM_RECORD=1` writes every request/response to `eval/cassettes/`; `LLM_REPLAY=1` serves them back. This powers the offline demo fallback and makes CI deterministic.
- **Acceptance**: switching a role's backend is a config change only; timeouts (planner 30 s, local 20 s) produce a fail-closed result; every call logs model id, latency, tokens, cost estimate.
- **Implemented behaviour**: typed errors (`LLMTimeout`, `LLMUnavailable`, `LLMSchemaError`, `PromptLeakError`, `CassetteMiss`, `LLMConfigError`); structured output via `json_object` mode (JSON Schema mode is not available in the Singapore region) or schema in the prompt for local models, validated with Pydantic with exactly one repair retry; retries with backoff only for connection errors, 429 and 5xx; cost in integer micro-USD; an INV-12 prompt guard runs before every request (live, record and replay) and blocks 9+ digit runs, key and TOTP seed shapes and registered secrets; message content is never logged. `mlx_lm.server` serves its model under the name `default_model`. Health check: `make models-check`.
- **Free-tier protection and failover**: a client-side sliding-window limiter per provider (Token Harbor: 50 requests/min and 1,500/hour, below the published 60 and 1,800) is shared by all roles and persisted in `logs/quota.json`. A 429 with `Retry-After` of at most 10 s is honoured once; anything else raises `QuotaExhausted` and sets a persisted cool-down (never a retry loop). A role may name `fallback_roles`: on `QuotaExhausted`, timeout, connection error or 5xx the call fails over (logged with the reason) and the local MLX server is started on demand; guard, schema, config, cassette and 4xx errors never fail over. `complete(..., provider=, model=)` forces a backend and disables failover for clean eval comparisons. Free routes (`:free`) cost 0; if a free or local route omits `usage`, tokens are estimated locally and `usage_estimated=true` (paid cloud without usage still fails closed).

### 5.3 `corridor.nlu`: code-mixed NLU cascade
- **Location**: `backend/src/corridor/nlu/`
- **Responsibility**: turn the user's own words into an `IntentFrame`.
- **Language plan** (decided 2026-10-10): **now** English, Nepali in Devanagari, romanised Nepali and code-mixed combinations, all fully evaluated. **Later, if time allows before submission**: Korean (`ko`, with a possible KRW to NPR corridor because a team member is from South Korea) and Simplified Chinese (`zh`). These replace Hindi in the plan. Design requirement: languages, number words, kinship terms, currency words, time words, clarification templates and festival calendars are data files under `data/lang/<code>/` and `data/calendar/`, listed in `data/lang/packs.yaml`; corridors are `data/seeds/providers.yaml`. Adding Korean or Chinese is data plus config only. The one code edit for a new currency is a line in `contracts/money.py` (`CurrencyCode` and its minor unit exponent, for example KRW has exponent 0).
- **Normaliser** (`corridor/nlu/normalise.py`, pure Python, no LLM): script and style detection, amounts (Devanagari digits, `20k`, `1.5 lakh`, `1,00,000`, number words in both scripts up to 100 and multipliers saya, hajar, lakh, crore), currency words (a bare `dollar` or `$` is ambiguous and never resolved), kinship terms, names, dates (relative, weekday, explicit, festival), conditions and recurrences, all with character-offset evidence spans.
- **Cascade confidence** is verifier based, never self-reported: `confidence_bps = 0` if the output is schema invalid, else `min(10000 - 500*[classifier abstains] - 1500*soft_failures, 6000 if any hard failure else 10000)`; hard failures are intent disagreement with the keyword classifier, amount mismatch or hallucination, currency conflict; soft failures are currency, deadline, payee, recurrence or condition without evidence. Below 8000 the cloud `parser_fallback` is called (ADR 0004).
- **Ambiguity and limits**: the model never selects among evidence candidates (several amounts, payees or dates are asked); input over 1000 characters or any internal error fails closed; festival dates over 200 days away are asked.
- **Provenance**: a slot backed by an evidence span in the user's own text is `USER_TYPED`; a slot the model filled without evidence is `MODEL_INFERRED`. Code attaches provenance, the model never does.
- **Intents** (closed set): `send_money`, `schedule_transfer`, `cancel_schedule`, `pay_invoice`, `check_balance`, `convert_currency`, `compare_routes`, `set_budget`, `spending_summary`, `add_payee`, `credit_passport_issue`, `credit_passport_share`, `explain_decision`, `help`, `other`.
- **Slots**: `amount`, `currency`, `payee_ref`, `source_account`, `deadline`, `recurrence`, `condition` (for example "only if rent is covered"), `purpose`.
- **Normalisation**: Nepali numerals (०-९), lakh/crore/hajar ("20 hajar" = 20,000; "1 lakh" = 100,000), relative dates and festival dates (Dashain, Tihar) via a lookup table in `data/calendar/`.
- **Cascade**: local parser first; if `confidence_bps < 8000` (0.80) or JSON invalid, call `parser_fallback`. Log cascade rate.
- **Acceptance**: on `eval/nlu/test.jsonl` (hand-checked, at least 200 utterances, not used in training): report intent accuracy, slot F1, cascade rate, p50/p95 latency, per language style. Target intent accuracy at least 0.90 for the cascade.

### 5.4 `models/finetune`: our own model (parser)
- **Location**: `models/finetune/`
- **Method**: distillation + LoRA on Apple Silicon with **MLX** (`mlx-lm`).
  1. `gen_data.py`: use the Qwen cloud model to generate about 3,000 to 5,000 labelled utterances from templates and personas, across language styles, including noisy spelling and code-mixing.
  2. Review a random 10% by hand and fix labels; write the **separate** test set (at least 200 utterances) by hand. An AI assistant may draft candidates, but every test item is checked by the developer, and test items never come from the training generator.
  3. `train.sh`: LoRA fine-tune Qwen3 small instruct (start with 1.7B; try 4B in 4-bit if memory allows) with `mlx_lm.lora`. Keep runs under about 45 minutes; the MacBook Air is fanless and throttles.
  4. Evaluate the base model vs fine-tuned vs cloud model on the same test set.
  5. Serve with `mlx_lm.server` and the adapter.
- **Fallback**: if local training is too slow, train the same recipe on a free cloud notebook GPU (Kaggle or Colab) and copy the adapter back. Training location does not change the claim.
- **Artifacts**: adapters and weights are git-ignored; publish the training data generator, config, and a model card (`models/finetune/MODEL_CARD.md`) with data size, hyperparameters, results, limitations.
- **Acceptance**: model card with a results table: base vs fine-tuned vs cloud, accuracy, slot F1, latency, cost per 1k requests.

### 5.5 `corridor.reader`: quarantined reader
- **Location**: `backend/src/corridor/reader/`
- **Responsibility**: read untrusted content (invoice images, PDFs, screenshots of messages, pasted SMS or email) and output **only** typed fields from allowlisted schemas.
- **Schemas**: `InvoiceDoc(issuer, amount, currency, due_date, beneficiary_name, beneficiary_account, reference)`, `MessageDoc(sender_label, claimed_relationship, requested_amount, requested_currency, urgency_markers[], contains_payment_instructions: bool)`, `StatementDoc(...)`.
- **Rules**:
  - The reader has **no tools** and no access to account data.
  - Output must validate against the schema; free-text fields are length-capped (120 chars) and stored as data, never placed into the planner prompt as instructions.
  - Every field is wrapped `Tainted[T]` with `Provenance.UNTRUSTED` and `source_doc_hash`.
  - PDFs are rasterised to images server-side; text layers are not trusted more than pixels.
  - Instruction-like content inside a document ("ignore previous instructions", "pay to account X instead") sets `contains_payment_instructions=True` and an `injection_suspected` flag that the policy engine and risk model consume.
- **Implemented (P2-2, ADR 0008)**: the planner receives only a typed summary built from enums, amounts, masked accounts and booleans; pasted third party text in chat is split off and read in text mode; `ReaderOutput` has an extra `notable_text` field (verbatim instruction-like lines, scanned by code, never shown to a model); injection signals are (a) patterns in `data/lang/*/injection_patterns.yaml`, (b) the model flag, (c) account differs from the stored beneficiary, (d) bank-details-changed claims.
- **Acceptance**: the poisoned-invoice fixtures in `eval/redteam/` never change the destination of an existing invoice payment without MFA, and never reach the planner as text (asserted by inspecting the recorded planner prompt).

### 5.6 `corridor.taint`: provenance tracking
- **Location**: `backend/src/corridor/taint/`
- **Types**:
  ```python
  class Provenance(StrEnum):
      USER_TYPED = "user_typed"        # the user's own words in this session
      USER_CONFIRMED = "user_confirmed"# user explicitly confirmed this exact value
      SYSTEM = "system"                # from the sandbox DB / trusted tool
      UNTRUSTED = "untrusted"          # from any document, image, message, third party
      MODEL_INFERRED = "model_inferred"# filled in by an LLM, not stated by anyone

  class Tainted(BaseModel, Generic[T]):
      value: T
      provenance: Provenance
      source_ref: str | None    # tool call id or document hash
  ```
- **Propagation**: any value computed from inputs takes the **lowest** trust of its inputs (`UNTRUSTED < MODEL_INFERRED < USER_TYPED < SYSTEM`, `USER_CONFIRMED` only via the confirmation flow).
- **Upgrade**: only `corridor.confirm` can upgrade a value to `USER_CONFIRMED`, and only for the exact value shown to the user. No code path lets an LLM output set provenance.
- **Acceptance**: property tests show provenance can never increase through arithmetic, string formatting, model calls or serialisation round trips.

### 5.7 `corridor.policy`: deterministic policy engine
- **Location**: `backend/src/corridor/policy/`
- **Interface**: `def evaluate(p: ActionProposal, s: AccountState, cfg: PolicyConfig, now: datetime) -> PolicyDecision`
- **Decision**: `ALLOW | REQUIRE_CONFIRM | REQUIRE_MFA | DENY` with `rule_ids: list[str]`, `reasons: list[str]` (human readable, translatable), `policy_version_hash`.
- **Rules** (each a small pure function, ID'd, documented in `docs/policy-rules.md`):

| Rule | Logic (illustrative values from `config/policy.yaml`) |
|---|---|
| `R-TIER-READ` | Read-only actions: ALLOW |
| `R-TIER-LOW` | Transfer to verified payee, amount at or under the low limit (for example NPR 10,000 equivalent), not tainted: REQUIRE_CONFIRM |
| `R-TIER-HIGH` | Above low limit, or new payee within 7 days, or schedule creation: REQUIRE_MFA |
| `R-DAILY-LIMIT` | Daily outgoing total above limit: DENY |
| `R-VELOCITY` | More than N transfers in 10 minutes: REQUIRE_MFA, then DENY |
| `R-TAINT-PAYEE` | Payee or account number with provenance UNTRUSTED or MODEL_INFERRED: REQUIRE_MFA, and DENY if it differs from the invoice's stored beneficiary |
| `R-TAINT-AMOUNT` | Amount with provenance UNTRUSTED not confirmed: REQUIRE_CONFIRM at minimum |
| `R-INJECTION` | Proposal derived from a document flagged `injection_suspected`: REQUIRE_MFA and show warning |
| `R-RISK` | Risk score at or above the high threshold: REQUIRE_MFA; at or above critical: DENY + safety lock |
| `R-PAYEE-ADD` | Adding a payee: always REQUIRE_MFA, never allowed from untrusted data alone |
| `R-LOCK` | Session locked (INV-11): DENY all outgoing |
| `R-SETTINGS` | Any change to limits, MFA, contact details: DENY in agent flow (must use the settings screen) |
| `R-CURRENCY` | Currency not supported on the selected corridor: DENY |
| `R-BALANCE` | Insufficient funds: DENY with explanation |

- **Combination**: most restrictive wins (INV-7). Unknown action type: DENY (INV-10).
- **Versioning**: `PolicyConfig` is loaded from YAML, canonicalised, SHA-256 hashed; the hash goes into every ledger entry.
- **Acceptance**: 100% branch coverage on `policy/`; Hypothesis property tests in `tests/properties/test_policy_invariants.py` covering at least: (a) no transfer above the low limit is ever ALLOW or REQUIRE_CONFIRM only, (b) any UNTRUSTED payee never yields ALLOW, (c) adding a rule can never make a decision less restrictive (monotonicity), (d) a locked session never yields anything but DENY for outgoing actions, (e) decisions are deterministic for identical inputs.

### 5.8 `corridor.confirm`: WYSIWYS grants and MFA
- **Location**: `backend/src/corridor/confirm/`
- **Canonical form**: proposals are serialised with sorted keys, minor units, normalised ids (RFC 8785 JSON canonicalisation style) and hashed with SHA-256.
- **Grant**: `ConfirmationGrant(grant_id, proposal_hash, user_id, level: CONFIRM|MFA, issued_at, expires_at = issued_at + 120 s, used: bool)`; stored server-side; single use.
- **MFA**: TOTP (RFC 6238) via `pyotp`; demo users have seeds in the sandbox; 5 failed attempts trigger the safety lock.
- **UI contract**: the confirmation card shows exactly the hashed fields, in the user's language, plus the policy reasons and any taint warnings. The grant is minted only from that card.
- **Acceptance**: tests for swap attacks (confirm A, try to execute B), replay (reuse a grant), expiry, and cross-user grants all fail closed.
- **Added acceptance criteria (security review, contracts pass 2)**:
  - Only `corridor.confirm` may create `Provenance.USER_CONFIRMED` values. A repo test greps for `USER_CONFIRMED`, `model_construct` and `model_copy(update=` on `Tainted` outside `corridor.confirm` and fails on any hit.
  - Refuse to mint a grant with `issued_at` in the future; refuse a grant whose level is below the freshly evaluated policy decision.
  - Never accept provenance from a request body: provenance is read from server-side stored state only.

### 5.9 `corridor.executor`: the only writer
- **Location**: `backend/src/corridor/executor/`
- **Steps** (single DB transaction): verify grant matches proposal hash, re-run policy (time-of-check = time-of-use), check idempotency key unused, apply balance changes, write transfer row, append ledger entry, commit. Any failure: rollback, ledger append of the failure in a separate transaction, safe error.
- **Acceptance**: concurrency test: 20 parallel executions with the same idempotency key produce exactly one transfer; balance never goes negative; ledger and transfers are consistent after random failure injection.
- **Added acceptance criteria (security review)**:
  - Reject a reused idempotency key regardless of whether the terms (amount, payee) match the earlier execution.
  - Re-read provenance of every value from stored state at execution time and re-run policy with it; never trust provenance carried by the request or the proposal object.
  - Require the grant level to be at least the level of the freshly re-evaluated decision.

### 5.10 `corridor.ledger`: hash-chained audit ledger
- **Location**: `backend/src/corridor/ledger/`
- **Entry**: `seq, ts, trace_id, actor (user|agent|system), event_type, payload_canonical_json, payload_hash, policy_version_hash, prev_hash, entry_hash = SHA256(prev_hash || seq || ts || payload_hash || policy_version_hash)`.
- **Merkle**: per session and per day, compute a Merkle root over entry hashes; store roots in `ledger_roots`; inclusion proofs available via API.
- **Verification**: `GET /ledger/verify` recomputes the chain and reports the first broken link. A console button runs it. A demo script tampers one row to show detection.
- **Append-only**: application DB role has INSERT and SELECT only on the ledger table (enforced in migration); updates and deletes are revoked. Implemented as the NOLOGIN role `corridor_ledger` used through `SET LOCAL ROLE` inside the caller's transaction, plus row triggers that refuse UPDATE and DELETE for every role (ADR 0007).
- **Wording**: always "hash-chained, tamper-evident audit ledger". Never "blockchain".
- **Acceptance**: tamper tests detect modification, deletion, insertion and reordering, and a change to any single column (including actor, event_type, trace_id).
- **Added acceptance criteria (security review)**:
  - The verifier rejects naive timestamps: any row whose `ts` loads without a timezone (for example from SQLite) is reported as a broken link, not a crash. Use `timestamptz` in Postgres and re-attach UTC for SQLite in tests. Any exception while verifying means "chain broken".
  - Genesis: the first entry has `seq == 1` and `prev_hash == GENESIS_PREV_HASH`; `seq` must be contiguous. Tests cover gaps, reordering and a forged genesis.
  - The verifier recomputes `payload_hash` from `payload_canonical_json`.
  - Ledger payloads contain no floats (canonical JSON rejects them).

### 5.11 `corridor.optimizer`: corridor routing
- **Location**: `backend/src/corridor/optimizer/`
- **Problem**: deliver amount `A` (in destination currency, or send amount in source currency) via up to `k=2` providers.
  - Cost of provider `i` for send amount `x_i`: `fee_fixed_i * y_i + fee_pct_i * x_i + fx_margin_i * x_i` where `y_i` is binary (provider used).
  - Constraints: `sum(x_i * effective_rate_i) >= A_dest`, `min_i * y_i <= x_i <= max_i * y_i`, `sum(y_i) <= k`, deadline: `eta_i <= hours_until_deadline` else `y_i = 0`.
  - Objective: minimise total cost in source currency + `lambda * expected_delay`.
- **Solver**: amended in P3-1 (ADR 0013): enumerate every single channel and every channel pair without a solver. Pair splits up to 20,000 feasible minor-unit allocations are scanned exhaustively; wider intervals scan the first and last 400 allocations. Wide-interval results are best among sampled candidates and are not guaranteed globally optimal. OR-Tools CP-SAT is used for the tuition funding plan (`optimizer/tuition.py`).
- **Output**: `RouteRecommendation(legs, total_cost_minor, effective_rate, delivered_minor, eta, savings_vs_default_minor, explanation_facts)`. `explanation_facts` are numbers for the grounding registry.
- **Acceptance**: unit tests on hand-computed cases; property test that the solution is never more expensive than the best single provider; cost replay (12 months per persona) reports annual savings vs a fixed default provider.

### 5.12 `corridor.risk`: scam and fraud risk model
- **Location**: `backend/src/corridor/risk/`
- **Features**: payee age, first transfer to payee, amount z-score vs user history, hour of day, velocity, corridor novelty, taint present, `injection_suspected`, reader urgency markers, claimed relationship mismatch, invoice beneficiary changed vs history.
- **Typologies** for the synthetic generator (`risk/synth.py`): emergency-relative scam, fake job-agency fee, lottery/prize, romance, invoice redirection (beneficiary swap), account takeover pattern, plus normal behaviour.
- **Model**: amended in P3-1 (ADR 0016): no LightGBM or SHAP (future work); a deterministic rule score with reason codes, thresholds `high` and `critical` in config.
- **Fallback**: if the model is unavailable, a documented rule-based scorer with the same interface.
- **Acceptance**: report PR-AUC, recall at 1% false-positive rate, and calibration on a held-out synthetic set; clearly state the data is synthetic in the limitations.

### 5.13 `corridor.passport`: Credit Passport
- **Location**: `backend/src/corridor/passport/`
- **Credential**: amended in P3-1 (ADR 0015): a signed credential, Ed25519 (EdDSA) with the `cryptography` package, canonical JSON payload with salted claim digests (SD-JWT in spirit, own small format, no `did:key`). Not a blockchain.
- **Claims**: months of history, count and ratio of on-time payments by category (rent, tuition, remittance, bills), corridors used, issued_at, expires_at. No raw transactions, no balances.
- **Verify**: `POST /passport/verify` and a public verify page in the console (paste token, see validity and claims).
- **Selective disclosure** (required): SD-JWT style, so the user can reveal only chosen claims (for example on-time rent ratio without remittance history) and the verifier can still check the signature.
- **Acceptance**: tampered token fails verification; expired token fails; claims match sandbox history.

- **Amendment (P3-1b, ADR 0015):** primitives are standard (Ed25519 via `cryptography`, salted SHA-256 digests); the format is SD-JWT style for the prototype and production would use a standard SD-JWT VC. Presentations are bound to an audience, a nonce and a holder signature, so a forwarded or replayed presentation fails; revocation is not built (expiry bounds it).

### 5.14 `corridor.grounding`: grounding verifier
- **Location**: `backend/src/corridor/grounding/`
- **Method**: every tool result registers its numeric facts (amounts, rates, fees, dates, counts) in the turn's `ToolResultRegistry`. After composition, extract numbers from the reply (handles Devanagari digits, lakh/crore formatting, currency symbols, thousands separators in en/ne/zh). Each must match a registered fact within tolerance (money exact in minor units after rounding; rates to 4 significant digits).
- **On failure**: regenerate once with a stricter instruction; if it still fails, return a templated reply built only from registry facts. Log both.
- **Implemented (P2-2, ADR 0009)**: `ground_reply` returns `pass`, `regenerated` or `templated`, emits a `grounding` event and ledgers failures. Rates are displayed to 4 significant digits (`format_rate`), exactly what is verified.
- **Acceptance**: hallucination-bait red-team cases ("what will the rate be next week exactly?") never produce an ungrounded number.

### 5.15 `corridor.agent`: planner and graph
- **Location**: `backend/src/corridor/agent/`
- **Framework**: LangGraph `StateGraph` with a checkpointer (Postgres in dev/demo, SQLite in tests) so confirmations can pause and resume (`interrupt`).
- **Nodes**: `ingest` → `read_untrusted` (if attachment) → `parse` → `clarify` (conditional) → `plan` → `run_read_tools` → `specialists` → `propose` → `policy` → `await_confirmation` (interrupt) → `execute` → `compose` → `ground` → `emit`.
- **Plan schema**: `PlanStep(id, tool, args: dict[str, Tainted], depends_on: list[id], kind: READ|PROPOSE)`; validator rejects cycles, unknown tools, more than 12 steps, PROPOSE steps without preceding READ steps for the data they use.
- **Interruption and rollback**: user can say "stop" or "change the amount" mid-plan; pending proposals are discarded and logged; nothing executed is reversed silently (executed transfers can only be reversed by a new, policy-checked action).
- **Prompts**: in `backend/src/corridor/agent/prompts/`, versioned files, English. Planner system prompt states the tool contract and that it cannot execute. Never put user secrets or full account numbers in prompts (INV-12).
- **Acceptance**: task-suite completion targets in section 8.
- **Added acceptance criteria (security review)**: when converting a `ProposalDraft` into an `ActionProposal`, trusted code checks that the source account, payee and invoice all belong to the session `user_id` (a model-chosen reference to someone else's account is rejected and ledgered). The model never supplies `user_id` or `idempotency_key`. Invoice payments take amount and payee from the stored invoice with their UNTRUSTED provenance. Prompts render money with thousands separators, never as a bare 9+ digit integer (the prompt guard blocks those).

### 5.16 `corridor.api`: HTTP surface
- `POST /chat` (SSE response stream of `AgentEvent`s), `POST /chat/{thread}/confirm` (proposal hash + optional `totp`; the server mints the grant), `POST /chat/{thread}/cancel`, `POST /unlock` (TOTP lifts the safety lock), `GET /dev/totp` (dev only, loopback only)
- `POST /attachments` (raw image or PDF body, at most 10 MB, type from magic bytes; runs the quarantined reader; returns the doc id, typed summary and injection signals, never the document's words). `POST /chat` takes `attachments: [{doc_id}]` and an optional `forwarded` text box
- `GET /accounts`, `GET /payees`, `GET /transfers`, `GET /schedules`
- `GET /ledger` (the caller's own entries), `GET /ledger/verify` (global, content free), `GET /ledger/proof/{seq}` (own entries); an operator view arrives with the console
- `POST /passport/issue`, `POST /passport/verify`
- `GET /console/decisions`, `GET /console/risk`, `GET /console/eval-report`
- `POST /admin/reset` (demo only, resets sandbox to seeds)
- **AgentEvent types**: `intent`, `clarify`, `plan`, `step_start`, `step_result`, `route`, `risk`, `taint_flag`, `policy_decision`, `confirm_request`, `executed`, `rejected`, `message`, `ledger_append`, `grounding`, `error`.
- Rate limiting (slowapi), max upload 10 MB, CORS restricted to the frontend origin.
