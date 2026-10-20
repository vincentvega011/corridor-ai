# ADR 0020: APP_ENV defaults to production

Status: accepted (P4-2).

## Context
`APP_ENV` defaulted to `dev`, so a deployment that forgot to set it exposed `/dev/totp`, `/dev/chat` and the sandbox authenticator,
which show authenticator codes. The security review of P4-1 flagged it.

## Decision
`AppEnv.PRODUCTION` is added and is the default. Every sandbox feature is an allow-list of `dev`, `demo`, `test` or `eval`, so
`production` matches none of them: no dev routes, no sandbox authenticator, no demo clock, no officer or compare routes, no
ablations, HTTPS enforced for model providers. `GET /clock` returns `sandbox: true` only for dev and demo, and the apps show the
persona switcher and the authenticator panel only then. The Makefile, `make demo`, the scripts and the test run set the environment
explicitly (`.env` of a developer keeps `APP_ENV=dev`). Test: `tests/integration/api/test_production_default.py`.

## Consequences
A deployment that sets nothing is a production API. Starting it needs a database (the checkpointer is Postgres unless `test`).
