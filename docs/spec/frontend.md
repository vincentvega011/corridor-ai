<!-- Frontend (section 6). Section numbers kept so old references still resolve. -->

## 6. Frontend

- **Stack**: Next.js (App Router) + TypeScript (strict) + Tailwind + shadcn/ui; React Flow for the plan DAG; Recharts for console charts; `next-intl` for `en` and `ne` (`ko` and `zh-CN` later, if time allows); Serwist (or equivalent) for the PWA service worker.
- **Fonts**: self-hosted via `next/font/local`: Noto Sans, Noto Sans Devanagari, Noto Sans SC. **No Google Fonts loader** (blocked in China).
- **Customer app** (`frontend/app/(customer)/`, mobile-first, installable PWA):
  - Chat with streaming replies, language auto-detected, quick replies for clarifications.
  - Confirmation card (WYSIWYS): exact fields, policy reasons, taint warnings in amber, TOTP input when required.
  - Wallet: balances per currency, recent transfers, schedules.
  - Attach button (camera/photo/PDF) that clearly labels content as "untrusted document".
  - Credit Passport screen: issue, show QR, share token.
  - Permanent SANDBOX banner.
- **Bank operations console** (`frontend/app/console/`, desktop):
  - Live trace: plan DAG (React Flow), each step's status, taint flags, policy decisions with rule ids.
  - Decision log: filter by decision, rule, user; drill into the proposal and its hash.
  - Ledger: entries, chain verification button, Merkle root, tamper demo toggle (demo env only).
  - Risk: score distribution, SHAP reasons per event.
  - Evaluation: renders `eval/reports/latest.json` (completion, ASR, baseline vs full, ablations).
  - Side-by-side mode: same input sent to `baseline` and `full`, shown in two columns. This is the demo's key moment.
- **Acceptance**: Lighthouse PWA installable; works at 375 px width; no network requests to blocked domains (CI check greps the build output for `fonts.googleapis`, `gstatic`, `google-analytics`).


## Dates in Nepali (P4-2)

Dates and times are shown as **Gregorian dates with Nepali month names** (for example "10 अक्टोबर 2026"), with Devanagari digits when
the customer turns that option on. We do not show Bikram Sambat (BS) dates. Reasons: (1) every date the system computes (invoice
due dates, deadlines, hold times, rate dates, the ledger) is Gregorian and has to match what a bank statement and a judge's
calendar show; converting each of them to BS adds a second source of error without adding information; (2) Nepali festival dates the
app resolves (Dashain, Tihar) are published as Gregorian dates in `data/calendar/festivals.yaml`, so the customer sees the same date the
agent used; (3) a BS conversion needs a lookup table that is not computable by formula and would be one more "illustrative" data set to
maintain. The month names are written by our own code (`frontend/lib/format.ts`) because not every browser ships Nepali locale data
(Playwright's Chromium does not). A BS display could be added later as a second line next to the Gregorian date.

## Browser tests and the demo run side by side

`make web-e2e` runs on its own ports (API 8100, web 3100, override with `E2E_API_PORT` and `E2E_WEB_PORT`), its own database
(`corridor_e2e`) and its own build directory (`.next-e2e`), and never attaches to an existing server, so it can run while `make demo`
(API 8000, web 3000) is up. The persona switcher and the sandbox authenticator also ask `GET /clock` (`sandbox`: true only for
APP_ENV dev or demo) before they show.

## Rate source on screen

Every screen that shows an exchange rate or a cross border amount names its source and date: "Rate: NRB reference, 9 Oct 2026,
illustrative" (en and ne). It appears on the Routes screen, the tuition plan and every confirmation card that has a cross border
route. A same currency payment has no exchange rate, so its card carries no rate line.


## Simplified Chinese (zh-CN) UI
A third display language for the customer app and the bank console. **Display only:** `useI18n().apiLang` maps `zh` to `en`, so every request body (`language` in button actions, the chat text) and therefore every parser, planner and composer request, and its cassette key, is the same as in English.
- Catalogues: `messages/zh.json` (customer, key for key with `en.json`), `messages/console.en.json` and `console.zh.json` (bank console; the console is English unless the UI is Chinese), `messages/server.zh.json` (Chinese versions of the server's fixed English reply and clarification templates, matched by their English text in `lib/serverText.ts`; text that is not a template, such as model-composed sentences, stays as the server sent it). Generators: `scripts/zh_build_messages.py`, `zh_build_console.py`, `zh_build_server.py`.
- Tests: `backend/tests/unit/test_locales.py` fails on a missing zh key, a placeholder mismatch, a console key missing in zh, or a server template without a Chinese version; `e2e/zh.spec.ts` checks the recorded housing deposit line still gives the card and hold card with the UI in Chinese.
- Formats: dates "2026年10月11日 14:15 AEDT" (zone named), money "AUD 2,000.00" unchanged, console times in Nepal time (NPT). The Devanagari digit option is hidden in zh-CN.
- Font: Noto Sans SC (SIL OFL 1.1, licence in `app/fonts/OFL-Noto-Sans-SC.txt`), variable weight 400 to 700, subset to the characters in the zh strings plus common banking characters by `scripts/subset_zh_font.py` (`app/fonts/noto-sans-sc-subset-wght.woff2`, about 154 KB). Latin text stays in Figtree and Plex. Self-hosted; the CSP is unchanged.
- Glossary: `docs/spec/glossary-zh.md`. Machine-assisted translation, pending native review.
