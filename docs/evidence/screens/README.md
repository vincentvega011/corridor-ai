# Screens (execution evidence)

Screenshots taken by the browser tests (`make web-e2e`, `frontend/e2e/*.spec.ts`) at 380 px width, in English
(`.en.png`) and Nepali with Devanagari digits (`.ne.png`). Synthetic data only. The SANDBOX banner and the demo date
(10 Oct 2026, before Dashain) are on every screen.

| File | What it shows |
|---|---|
| 00-chat-home | Chat with the persona switcher (marked as a sandbox feature) |
| 01-housing-card | Proposal card for the payee in the housing deposit PDF: every field, warnings, MFA entry, countdown |
| 02-housing-saved-held | After confirming: saved, held, scam warning, earliest time in the user's zone, "ask the bank to verify" |
| 03-payees-hold, 05-payees | Payees with provenance labels and hold status |
| 06-routes-priya, 20-routes | Route comparison (server numbers) |
| 07-family-empty, 12-family-incoming, 13-plan-accepted | Contributor screen and the plan status after the uncle accepts |
| 10-tuition-plan | The funding plan: a request to the uncle and two payments |
| 08-passport-empty, 31..34 | Credit Passport: share card, presentation, verifier result, a forwarded presentation rejected |
| 09-verifier, 30-verifier-challenge | The public verifier page (landlord) issuing a challenge |
| 40..42 | A payee typed by the user: card, saved with hold, verified by the bank |

Not captured yet (they need the cloud planner): the same-day payment refusal and
the MFA payment after verification (t20), and the three confirmation cards of the tuition plan.
