# 9. Grounding verifier

## Status

Accepted, 2026-10-11.

## Context

INV-9: any amount, rate, fee, balance or date shown to the user must trace to a tool result from the same
turn. A composer model can invent a plausible figure, especially when asked about the future.

## Decision

1. **Registry.** `ToolResultRegistry` collects the numeric facts of every tool result of the turn (money in
   minor units with currency, rates, counts, dates, basis points from facts labelled `bps`), plus the
   user's own typed values (amount with currency, deadline) marked as user facts, plus the executed
   receipt amounts. A model cannot add to it.
2. **Extraction** (`grounding/extract.py`): Devanagari digits, Western and Nepali grouping, `lakh`, `crore`,
   `hajar` and their Devanagari forms, currency codes and symbols (`Rs`, `रू`; a bare `$` never resolves to
   one currency), percentages, rates, ISO and written dates. Identifiers (`inv_..._2026s2`, `****4471`,
   `S2026-0417`) are removed first. "1 AUD = ..." names a unit and is not an amount.
3. **Matching.** Money must be exact in minor units and currency after rounding; a percentage maps to basis
   points; a rate matches at 4 significant digits; dates and counts match exactly; a bare number of 100 or
   more must equal a registered count or a registered money amount in major units. Any decimal that is not
   money, a percentage or a date is treated as a rate claim.
4. **Policy.** `ground_reply`: pass; otherwise regenerate once with a stricter instruction that lists the
   numbers that were not in the facts and forbids estimates; if that fails, a templated reply made only from
   registry facts. Each outcome (`pass`, `regenerated`, `templated`) is emitted as a `grounding` event, and a
   failure writes a `grounding_failure` ledger entry with both attempts.
5. **Display precision matches verification.** `format_rate` now shows 4 significant digits (it showed 4
   decimals, which carried more precision than the verifier checked). The full 8 place rate stays in the
   facts. This replaces the 4-decimals note from P0-4.

6. **Which replies are verified.** Every reply that can contain model text or a computed figure passes the
   `ground` node (read replies, receipts). The registry holds only the tool results the reply may discuss,
   so a history fact cannot legitimise a number in a balance reply. A bare `$` can only match a USD or AUD
   fact. Replies that the code assembles from typed fields (clarifying questions, the document summary
   shown after an upload, policy reasons) contain no model-generated number: a document's own amount is
   quoted back explicitly labelled untrusted.

## Limits

Numbers written as words ("five thousand"), spaced digits ("5 0 0 0") and amounts a model hides in
non-numeric forms are not extracted; the composer prompt asks for digits, and the templated fallback is
built from facts. Review round two added: any letter case of `Rs`/`NPR` glued to digits or separated by `-`, `_` or `.`, `NPR 500 000` and Arabic thousands separators, `million` and `hundred`, `15th Nov 2026`, and day-month dates without a year (never groundable). `1 AUD = 90.12 NPR` is read as a rate, the Indian rupee sign never grounds against NPR, and a reference such as `ref 20261115` is not an amount. Extraction does handle glued currency ("NPR5000", "5000NPR"), the `k` suffix, written
dates, numeric dates and every Unicode decimal digit.

The verifier checks that numbers are grounded, not that sentences are true: a model could attach a true
number to a wrong claim. Replies the system writes from fixed templates are verified too, because they are
built from the same facts. Small bare integers (below 100, without a unit) are not treated as claims about
money.

## Amendment (P2-3): written numbers and extraction hardening

The grounding extractor now reuses the NLU normaliser's number-word parser, so "twenty thousand", "bis hajar" and "ek lakh" (Devanagari and romanised) are extracted and checked. Also covered: colon forms (`NPR:500000`), digits glued to letters, `lac`/`cr`, `Rs`/`रू`/`$` after the number, and year-less dates (matched by month and day). Unsupported currencies such as INR never ground. Known limit: number words above the normaliser's range are not understood; replies over the length limit are blocked rather than checked.
