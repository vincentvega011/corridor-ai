# 3. No floats anywhere in the safety path

## Status

Accepted, 2026-10-10. Supersedes the note in ADR 0002 that scores and confidences stay floats.

## Context

Canonical JSON (ADR 0002) rejects floats, because float formatting is not stable across languages and
a float can hide rounding in a value that is hashed or compared by policy. Risk scores and parser
confidences were floats, so any ledger payload that recorded them would have failed (fail closed, but
it would have broken INV-8 flows). Floats near thresholds also make policy comparisons hard to audit.

## Decision

- Money is integer minor units. Rates are Decimals quantised to 8 places. Ratios, scores and
  confidences are integers in basis points (0 to 10000), type `Bps` (signed variant `SignedBps`).
- `RiskAssessment.score` is now `score_bps`; `RiskReason.contribution` is `contribution_bps`;
  `IntentFrame.confidence` and `ParserOutput.confidence` are `confidence_bps`.
- Thresholds in `config/policy.yaml` and the NLU cascade (0.80 becomes 8000 bps) are integers.
- The integer validator rejects bool, float and numeric strings, as for money.
- A test asserts that no public contract model has a float typed field.
- Model providers return floats in some places (for example logprobs); such values must be
  converted to integers at the gateway edge and never enter a contract.

## Consequences

- Every risk and intent value can be ledgered and hashed without a DTO.
- `HASH_SCHEMA_VERSION` is unchanged: none of the renamed fields is part of the proposal hash.
- Model code (LightGBM, SHAP) must round to basis points with a documented rounding rule
  (ROUND_HALF_EVEN) before building a `RiskAssessment`.
