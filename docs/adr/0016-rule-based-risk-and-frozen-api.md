# 0016. Rule based risk score and the frozen API contract

Date: 2026-10-12. Status: accepted (P3-1 Part 5).

## Context
`docs/spec/modules.md` 5.12 planned a LightGBM classifier with SHAP reasons, with a rule scorer as fallback. The
P3-1 brief cut the classifier (no time to train, evaluate and defend a model on synthetic data) and asked for a
deterministic score with reason codes. The brief also asks to freeze the API the two front ends need, contracts
first, with a separate role and separate auth for bank officers.

## Decision
1. **Risk score.** `corridor.risk.score.assess(proposal, state, config, now)`: fixed weights in basis points
   (`config/policy.yaml`, hashed into the policy version) for nine reasons: `new_payee`, `untrusted_payee`,
   `held_payee`, `document_derived`, `amount_unusual` and `amount_extreme` (against the user's median payment of
   the last six months, from the database), `unusual_hour` (the user's local time zone, small hours),
   `recent_denials` (safety lock window) and `cop_mismatch` (an unverified payee whose name does not match the
   registered holder). The sum is capped at 10000; 6000 asks for MFA, 9000 denies. The three largest reasons are
   returned. No model, no randomness, pure function (INV-2); `corridor.risk` is covered by the same import boundary
   test as policy.
2. **Only stricter.** R-RISK takes the larger of the score a proposal carries and `assess`, and returns only
   REQUIRE_MFA or DENY. The engine combines rules with max (INV-7). A Hypothesis property test
   (`tests/properties/test_risk_properties.py`) removes R-RISK and shows the decision never gets stricter without
   it, over random payee provenance, age, CoP status, denials, history and time zone.
3. **Consequences seen in the suite.** A CoP mismatch with an untrusted payee is critical on its own (3000 +
   6000), so the scam payee of the housing demo is now refused after the 24 hour hold as well (before: allowed up to
   NPR 5,000 with MFA). A critical score on a proposal built from documents counts as one document denial and does
   not lock the user (ADR 0006: a poisoned document must not lock its victim); a critical score on a typed request
   still locks at once (INV-11).
4. **Audit.** Every `policy_decision` ledger entry and event carries `risk` (score, level, reason codes with
   weights) so the officer console can show why.
5. **API freeze.** Request and response models for every frozen route are Pydantic models in
   `corridor/contracts/api.py` (event `data` models allow extra keys). `scripts/gen_contracts_ts.py` writes
   `frontend/lib/contracts.ts` from their JSON Schema, offline (`make contracts`); `make contracts-check` and a unit
   test fail when the file is stale. `docs/spec/api.md` lists every route with real examples, produced by a test
   that also validates them against the models.
6. **Officer role.** `corridor.api.officer`: ledger view with chain verification, proposal trace, risk queue, baseline
   against full for a case id and the evaluation summary, all read only, plus the existing payee verification. They need
   the officer secret (`officer_id_for`); a customer token or none gets 401 on every one of them (a test enumerates
   every `/officer` route), and they are 404 when officer access is off. New customer routes: `GET /proposals/pending`,
   `POST /routes/compare`, `GET /tuition/plan`; the passport routes of ADR 0015.

## Consequences
Contract additions only: `RiskAssessment` is unchanged; `AccountState` gained `timezone`, `history_median_npr_minor`,
`recent_denials` and per payee `cop`. Limits: weights are illustrative and untuned (no data to tune on); the risk
score is not a fraud detector; one shared officer secret; pending proposals are an in memory cache. LightGBM and SHAP
are recorded as future work.

## Amendment (P3-1b): exactly when a request is critical
Critical means a score of 9000 or more (`risk.critical_bps`); the sum is the weights below for the reasons that
apply, capped at 10000. Weights (basis points): cop_mismatch 6000, amount_extreme 3500 (more than 10 times the user's
median payment), untrusted_payee 3000, held_payee 2500 (only with an untrusted payee), amount_unusual 2000 (more
than 3 times the median; replaced by amount_extreme above 10 times), new_payee 1500 (saved under 30 days ago),
document_derived 1500, unusual_hour 1500 (00:00 to 05:00 local), recent denials 1000 each up to 3000.

1. **Without a name mismatch, an untrusted payee or a recent denial, a request is never critical.** The most such a
   request can reach is new payee 1500 + extreme amount 3500 + small hours 1500 + document 1500 = **8000**
   (high: MFA, not a denial). This covers every payee the user typed or the bank holds.
2. **A name mismatch (Confirmation of Payee) on an unverified payee is 6000.** Critical together with any one of:
   an untrusted payee (3000), an extreme amount (3500), three recent denials (3000), or two of new payee, document,
   small hours, unusual amount, two denials.
3. **An untrusted payee (3000) is critical** only together with at least 6000 more: for example extreme amount
   plus held payee, or extreme amount plus three recent denials.
4. **Recent denials add at most 3000 and need three denials in the window to be critical with a typed payee at
   its worst.** Three denials also lock the user (INV-11).
`tests/unit/risk/test_critical_conditions.py` enumerates the states and fails if a weight change breaks 1, 2 or 4.

**Priya's new landlord (typed by her, AUD 2,000 deposit).** Not critical in any state (worst case 8000), no lock.
Two days after she saves the account the payment needs MFA (a payee under 30 days old). On the day she saves it,
the existing `R-PAYEE-COOLOFF` first payment cap (NPR 5,000 equivalent within 24 hours for an account the user typed)
refuses it; that is the cooling off rule and not the risk model, and one refusal does not lock. No rule was
changed. `tests/integration/test_typed_landlord_risk.py`, task t19.
