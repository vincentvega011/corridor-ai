# 0017. Infrastructure errors are not outcomes, and the evaluation can resume

Date: 2026-10-12. Status: accepted (P3-1 follow-up).

## Context
The P3-1 evaluation re-run stopped when the cloud provider answered 429 with a one hour wait. Cases that
followed failed with `error:plan_failed` and the merged report counted them as failed tasks and as blocked
attacks. A planner that never answered had "blocked" every attack and the full system looked like it completed
half its tasks. Neither number says anything about the system. The report was caught by eye, not by the code.

## Decision
1. **A fourth outcome, `infra_error`.** A case that stops on `QuotaExhausted`, `LLMTimeout`, `LLMUnavailable` or
   `TimeoutError` records `infra_cause` and its outcome is `infra_error`. It is never blocked, contained or
   harmful. Schema failures and prompt guard stops are not infrastructure: they are the system's own behaviour
   and still count. Results written before the field existed are classified from their error text, and a bare
   `error:plan_failed` counts as infrastructure.
2. **No publishing over infra errors.** `report.write_report` refuses when any baseline or full row is an
   `infra_error`. It keeps the raw results (`publishable: false` and the case list in `meta`), writes no tables
   or charts, does not touch `latest.json`, and exits with the affected cases listed. Ablation rows are not
   headline rows. `merge.py` goes through the same function.
3. **Resume.** `eval/run.py` saves every result after each case to `eval/reports/checkpoint.json`. On a quota
   stop it exits with code 75, prints when the provider said it would accept calls again, and stops instead of
   failing every remaining case. `--resume` (or `--resume <report dir>`) keeps every finished result and runs
   again only the cases that hit infrastructure errors. A resumed run keeps the original start time.
4. **Which limit.** On a 429 the gateway logs the rate limit headers (never the body or a key) as
   `provider_429`.

## Consequences
A run can be stopped and continued without paying twice for finished cases. A stale or interrupted run can no
longer become the headline report by accident.
