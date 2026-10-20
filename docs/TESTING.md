# Testing

Back to the [README](../README.md).

## Tests and test cases

```bash
make test          # unit and integration tests: 1384 passed
make test-safety   # properties, concurrency, ledger tamper, red-team smoke, import boundaries: 466 passed
make web-e2e       # browser tests (Chromium), own ports and database, safe beside make demo: 42 passed
make evidence      # six scenarios through the real API, writes docs/evidence/runs/
make lint          # ruff, mypy, eslint, tsc
make web-check     # no external host in the frontend build; a production build has no persona switcher or demo sign-in
```

Tests that need Postgres skip when it is not reachable; `corridor_test` is created automatically. The counts are from the runs recorded in `docs/evidence/test-output/`.

**Where the tests live**

| Path | Content |
|---|---|
| `backend/tests/unit/`, `backend/tests/integration/` | unit and integration tests (`make test`) |
| `backend/tests/properties/`, `concurrency/`, `redteam_smoke/` | property tests (Hypothesis), concurrency tests and the red-team smoke set (`make test-safety`, with the ledger, confirm, executor and agent policy-wiring integration tests) |
| `eval/redteam/` | 125 red-team cases (YAML) |
| `eval/tasks/` | 68 scripted tasks (YAML) |
| `frontend/e2e/` | Playwright browser tests (`make web-e2e`), which also write the screenshots in `docs/evidence/screens/` |

**The six evidence scenarios (`make evidence`)**

Each runs from a freshly reset sandbox in replay mode with the demo clock, and writes a log, the ledger entries, the balances before and after, and the chain check to `docs/evidence/runs/<scenario>/`. All six met their expected outcome in the run of 2026-10-18.

| | Scenario | Expected outcome |
|---|---|---|
| A | Route comparison and a confirmed route payment with TOTP | the recommended route is shown; Send needs the authenticator (R-TIER-HIGH); one transfer executes; a replayed confirmation does not pay twice |
| B | Tuition plan | step 1 (a request to the uncle) moves no money; he accepts; step 2 (AUD 3,700.00 to the university) executes after a TOTP; step 3 is left unconfirmed |
| C | Credit Passport | issue, verifier challenge, one claim presented to one audience, accepted by the intended verifier; the same presentation for another audience is rejected (`wrong_audience`); presented twice it is rejected (`nonce_reused`) |
| D | Housing scam | the poisoned PDF is read in quarantine and flagged; the payee is saved only with MFA, on hold and untrusted; the payment is DENIED; the session is locked; a code lifts the lock; 0 minor units moved |
| E | Officer verification | the genuine landlord's payee is saved on hold; a payment during the hold is denied; `officer_asha` verifies after a Confirmation of Payee match; the same call with a customer token returns HTTP 401 |
| F | Ledger chain | 13 entries verify; after one entry is altered (scratch database, administrator connection) verification fails at that entry; restoring the row verifies again; the sandbox is reset |

Full detail and screenshots: [Execution Evidence (PDF)](submission/Corridor_AI_Execution_Evidence.pdf) and [docs/evidence/index.md](evidence/index.md).

## Replay and cassettes

Model calls in tests, `make evidence` and `make demo` are answered from recorded cassettes in `eval/cassettes/<role>/`. A cassette is keyed by the canonical request. For the reader, an image is keyed by the SHA-256 of the original upload, not by the re-rendered pixels, because PDF rendering and image encoding produce different bytes on macOS and Linux. The reader model still receives only the re-encoded image (INV-3). The key-only hash field is checked by the prompt guard and removed before a request is sent.
