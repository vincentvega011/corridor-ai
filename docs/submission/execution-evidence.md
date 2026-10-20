# Corridor AI Execution Evidence

The full evidence package, with a table mapping each claim to the exact file, is **[`docs/evidence/index.md`](../evidence/index.md)**. This page summarises it.

## Standard

A passing run is evidence that the listed code paths behave as stated in the synthetic sandbox. It is not evidence of production safety or live-bank operation. Evaluation numbers are copied from a published report and carry its run id and limits.

## What is in the package

- **Six scenario runs** (`make evidence`, `docs/evidence/runs/`), driven through the real API in cassette replay mode with the demo clock, each from a freshly reset sandbox, each with a readable log, the ledger entries it produced (masked), balances and transfers before and after, and the chain verification output:
  A. route comparison and a confirmed route payment with TOTP; B. tuition plan (request to the uncle, he accepts, step 2 paid with TOTP); C. Credit Passport (issue, verifier challenge, valid presentation, forwarded copy rejected, replay rejected); D. housing scam (poisoned PDF, payee saved on hold, payment denied, session locked, unlocked with MFA); E. officer verification of the genuine landlord (CoP match, out-of-band method, customer token refused); F. ledger chain verification, tamper detection, restore, reset. All six met their expected outcome on 2026-10-18 (`docs/evidence/runs/results.json`).
- **Test output** (`docs/evidence/test-output/`): `make test` 1384 passed; `make test-safety` 466 passed; `make web-e2e` 42 passed; `make lint` exit 0.
- **Published evaluation report** `20261011T175100` (`docs/evidence/eval/`): one seed, 11 tasks, 72 attacks, an earlier build (11 October 2026), before the risk score. Fooled user: baseline moved NPR 1,018,730.00, Corridor AI NPR 0.00; cautious user: baseline NPR 1,372,847.45, Corridor AI NPR 0.00. The full system saved 7 attacker payees on hold for the fooled user (contained, no money moved). A historical preview, not a result for the current 68-task and 125-case suite.
- **Screenshots**: `docs/evidence/screens/` (customer app, English and Nepali), `screens/console/` (bank console), `screens/zh/` (Chinese UI, machine-assisted translation).
- **Security self-assessment**: `docs/submission/security-self-assessment.md`.

## Limitations

- Replay mode shows repeatable behaviour from recorded model answers, not live model behaviour. No live provider call is part of this package.
- The expanded suite (68 tasks, 125 red-team cases) has no fresh evaluation because the cloud planner is quota-blocked. The old report stays labelled a one-seed preview.
- No external penetration test, cryptographic audit or regulatory review was performed.
- All data is synthetic. Do not add `.env`, secrets, TOTP seeds or complete account identifiers to an evidence package; the run logs are scrubbed of them.
