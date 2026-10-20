# 4. Verifier based confidence and evidence backed provenance in NLU

## Status

Accepted, 2026-10-10.

## Context

The NLU cascade (docs/spec/modules.md 5.3) calls a small local model first and a cloud model when the local result
is not trustworthy. Two questions decide whether this is safe. How is "trustworthy" measured, and how do
we stop a model from smuggling an invented value into a slot that later policy treats as something the
user said (INV-4)? Self reported model confidence is poorly calibrated, and small models are confidently
wrong. Models can also copy or invent numbers, currencies and payees.

## Decision

1. **Deterministic evidence first.** A pure Python normaliser (no LLM) extracts amounts, currencies,
   kinship and name references, dates, festivals, conditions and recurrences from the user's own text.
   Every candidate carries a character span into the original text. Vocabulary lives in data packs
   (`data/lang/<code>/`, `data/calendar/`), not code.
2. **Confidence is computed, not reported.** `confidence_bps` is 0 when the model output is schema invalid,
   otherwise `min(10000 - 500*abstain - 1500*soft, 6000 if hard else 10000)`. Hard failures: intent
   disagrees with the keyword classifier, amount mismatch or an amount with no evidence, currency
   conflict. Soft failures: currency, deadline, payee, recurrence or condition without evidence. The
   model's own confidence field is required by the schema but ignored. Below 8000 the cloud parser runs.
3. **Provenance comes from evidence, set by code.** A slot value that matches a normaliser candidate is
   `USER_TYPED` with `source_ref = user_text:<start>-<end>`. A value the model produced with no evidence
   is `MODEL_INFERRED`. When model and evidence disagree, evidence wins. The model has no way to express
   provenance (`llm_outputs` has no such field, ADR 0002), so it cannot upgrade a value.
4. **Ambiguity is never resolved by guessing.** A bare "dollar" yields an ambiguous currency with options
   AUD and USD; several payee or amount candidates yield an ambiguity. Both become a templated
   clarification question in the user's language style, without an LLM.
5. **Prompts are guard safe.** Digit runs of 9 or more are replaced by a placeholder before any model sees
   the text (INV-12); the hints use major units, so even "5 crore" stays below the guard's limit.
6. **Fail closed.** If both models fail or confidence stays below the threshold after the cascade, the
   frame is intent `other` or the parse asks the user to rephrase; nothing is guessed (INV-10).
7. **Festival dates are data with sources.** `data/calendar/festivals.yaml` holds only dates with a source;
   an unverified or missing date yields "unknown" and the agent asks, never a guess.

8. **Review follow-ups** (security-reviewer pass 1 blocked on two items, both fixed):
   - The model never selects between evidence candidates. Several amounts or payees are ambiguous and the user is
     asked; the only deterministic tie-break is a single amount with a currency attached (so a pasted account
     fragment cannot be chosen by model output). A model amount is used only when the text has no number at all.
   - `parse` caps input at 1000 characters, the normaliser at 2000, numbers are parsed with ASCII digits only and
     malformed ones (1.2.3) are not numbers, and any unexpected exception returns the fail closed frame.
   - Unsupported currency words cannot be replaced by a model guess; model deadline and recurrence values are
     validated; `purpose` is typed only for whole tokens and stores the user's own text; blocked text is not kept;
     after a cascade the higher confidence result wins; redaction reuses the prompt guard's own logic.
   - A festival date more than 200 days away, or several different dates, are asked and not assumed. Festival
     slots carry the source grade (official calendar site or computed panchang) and a conflict flag.

9. **Festival semantics** (decided 2026-10-10, supersedes the day-before rule of item 7): "before Dashain",
   "Dashain aghi" and "dashain bhanda agadi" mean on or before Vijaya Dashami (the Tika day, main day). "before
   Dashain starts", "dashain suru huna aghi" and "ghatasthapana aghi" mean Ghatasthapana (the separate
   festival `dashain_start`). The same main day rule applies to Tihar (Laxmi Puja) and the others; there is no
   "day before" arithmetic. If the resolved deadline is today or already past, the agent confirms with the
   user instead of assuming. Unknown payee names that appear in the user's text ("bob") are kept as payee
   candidates so the agent can ask who they are; a name with no span in the text is never kept as typed.

## Consequences

- A value flagged `MODEL_INFERRED` can still reach policy (for example a currency implied by context), but
  policy knows it was not stated and can require confirmation.
- The formula's weights are design choices, not learned. The boundaries are tested; the preview metrics
  on draft labels show how often the cascade fires.
- Adding Korean or Chinese needs a language pack folder and eval items, not new code, except new currency
  codes in the contracts.
- The normaliser is conservative: unusual spellings or number words not in the packs simply produce no
  evidence, which lowers confidence and triggers the cascade or a clarification.
