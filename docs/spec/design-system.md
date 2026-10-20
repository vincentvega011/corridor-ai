# Design system: "safety you can see"

Scope: `frontend/` only (customer PWA and bank console). No behaviour, route, data flow or API change. Screens: `docs/evidence/screens/` and `docs/evidence/screens/console/`.

## 1. Concept

The product's idea is that the AI can be fooled but the money cannot move. So the interface shows one thing consistently: **where a value came from and what the system will let it do.** Trust state is the visual language, designed once and used everywhere:

- **Provenance** answers "can I rely on this value?": Verified, Typed by you, From document (untrusted), Held.
- **Outcome** answers "what happens next?": Allowed, Needs confirmation, Needs authenticator code, Denied.

Neither relies on colour (WCAG 1.4.1): the theme is monochrome, and every state is told apart by an icon, a text label, a border style and a weight.

Mood: a restrained, engineered fintech tool. Black, greys and white; calm, precise, high contrast; no gradients, no shadows, no decoration. The customer app is light; the bank console is dark by default (light on request). There is exactly one functional colour, a muted red, reserved for blocked, denied, critical and FAIL states. PASS and verified states are black or white with a check icon, never green.

## 2. Tokens

All tokens live in `frontend/app/tokens.css` (CSS variables, mapped into Tailwind v4 in `@theme`). Nothing else defines a colour or a font. The legacy token names (`pine`, `saffron`, `verified`, `typed`, `doc`, `held`) are kept so components need no change; they now resolve to greys.

### Colour (customer, light)

| Token | Value | Use |
| --- | --- | --- |
| `--color-paper` | `#FFFFFF` | app background, inputs |
| `--color-surface` | `#FAFAFA` | cards |
| `--color-sand` | `#F4F4F5` | subtle surface, context strip, quiet fills |
| `--color-line` / `--color-line-strong` | `#E4E4E7` / `#D4D4D8` | borders; input and secondary button borders |
| `--color-ink` | `#09090B` | text (19:1 on white) |
| `--color-muted` | `#52525B` | secondary text (7.7:1 on white, 7.0:1 on sand) |
| `--color-faint` | `#71717A` | placeholders and icons only (4.8:1 on white) |
| `--color-pine` / `-strong` / `-soft` | `#09090B` / `#27272A` / `#F4F4F5` | primary action (solid black, white text) |
| `--color-doc` | `#3F3F46` | From document (untrusted) text and dashed border |
| `--color-deny` | `#B91C1C` | the one functional colour: Denied, blocked, critical, FAIL (6.5:1 with white text) |
| `--color-on-solid` | `#FFFFFF` | text on a black or red fill |

Secondary actions are white with a 1 px `--color-line-strong` border. Focus ring: 2 px `--color-ink` (white in the dark console) with 2 px offset on every interactive element.

### Colour (console, dark default)

Background `#09090B`, surface `#18181B`, raised `#27272A`, borders `#3F3F46` (dividers `#27272A`), text `#FAFAFA`, secondary `#A1A1AA` (7.9:1 on background), primary action `#FAFAFA` with `#09090B` text, functional red `#F87171`. The light variant (`data-theme="light"`) uses the customer values. Toggle in the header, remembered per browser (`localStorage`, a display preference only).

### Type

| Role | Face (OFL, self-hosted in `app/fonts`, licence files beside them) |
| --- | --- |
| All UI text and headings | **IBM Plex Sans** 400/500/600. Headings: weight 600, letter spacing -0.01em, no serif |
| Amounts in tables, hashes, fingerprints, codes | **IBM Plex Mono** 400/500 |
| Devanagari | **Noto Sans Devanagari** 400/600/700 |
| Simplified Chinese | **Noto Sans SC** subset (`scripts/subset_zh_font.py`) |

Fraunces, Figtree and Noto Serif Devanagari are no longer used; their files and licence texts were removed. All numbers use `font-variant-numeric: tabular-nums` (set on `body`).

Scale (customer, rem at 16 px): `h1 1.625rem/1.2`, `h2 1.0625rem/1.3`, `body 1rem/1.5`, `small 0.875rem/1.4`, `caption 0.75rem/1.35`, `code 0.8125rem`. Console: `base 0.8125rem/1.45`, `small 0.75rem`.

### Space, radius, depth

Space: 4 px base, steps 4, 8, 12, 16, 24, 32, 48. Radius: 6 px for cards, inputs and buttons (`--radius-sm`, `--radius-md`), 4 px for chips and badges (`--radius-chip`), 8 px at most anywhere (`--radius-lg`, the signed instrument and credential card). Only the language switch is pill shaped. No drop shadows and no gradients: hierarchy comes from 1 px borders and spacing (`--shadow-e*` are `none`).

### Motion

Card entrance 220 ms ease-out, countdown ring linear 1 s steps, verification check draws in 380 ms, skeletons pulse in opacity. All animation sits inside `@media (prefers-reduced-motion: no-preference)`.

## 3. Trust-state components (`frontend/components/ui/`)

| Component                              | Contract                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `TrustChip kind`                       | `verified` (shield with check, **solid black fill**, white text), `typed` (pencil, **1 px solid outline**), `document` (page with fold, **dashed outline**, "untrusted"), `held` (clock with lock, outline on a **light grey fill**). Props: `kind`, optional `label`. Always renders icon + text; `data-trust` carries the kind for tests                                                                                                                                                                                                  |
| `OutcomeBadge level`                   | `allow` (check in circle, solid black), `confirm` (hand tap, 1 px outline), `mfa` (six dots, 2 px outline on grey, bold), `deny` (octagon cross, the functional red, solid). One badge for the policy decision on cards, the console trace, the risk queue and the ledger                                                                                                                                                                                                                                                           |
| `SignedInstrument` (in `ProposalCard`) | The confirmation card as a signed instrument: header band (action, step n of N, outcome badge), field grid (every field the grant binds, label above value, tabular figures), fingerprint line (first 8 and last 4 hex, monospace, copy-safe full hash in details), 120 s grant countdown as a slim ring (SVG attributes only: CSP forbids inline style), authenticator entry as six large boxes backed by **one** real `<input>` (paste, autofill and screen readers work), a seal strip "Bound to this exact proposal" |
| `CountdownRing`                        | `secondsLeft`, `total`; stroke-dashoffset attribute; turns mid grey under 30 s, red under 10 s; text alternative via `role="timer"` with the seconds                                                                                                                                                                                                                                                                                                                                                                     |
| `CodeBoxes`                            | six cells, filled/active states, caret, numeric keyboard, Western digits only                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `HoldTimeline`                         | three nodes: **Saved now** (done), **Held until {time, zone}** (current), **Then capped until the bank verifies** (next). Grey and calm; reasons below as plain text                                                                                                                                                                                                                                                                                                                                                      |
| `Timeline`                             | generic vertical stepper used by the tuition plan (sources, routes, arrival before the due date)                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `EmptyState`, `Skeleton`               | designed empty and loading states with an icon, one sentence, and where useful the next action                                                                                                                                                                                                                                                                                                                                                                                                                           |

Rendering rules that do not change: all model, document and payee text is plain text (no `dangerouslySetInnerHTML`, enforced by `check-no-external`); the client never computes money (it formats strings the server sent; `toMinorUnits` only parses what the user typed, as before); the session cookie stays httpOnly.

## 4. Layout

Customer: mobile first at 380 px. Under `lg` (1024 px) a fixed **bottom navigation** with five items (Chat, Payees, Routes, Tuition, More); "More" opens Family, Credit Passport, Verifier page. From `lg` a left **sidebar** with all seven items and the persona and language controls. A slim **context strip** (neutral grey bar, black text, flask icon) shows "SANDBOX: no real money" and the demo date on every screen; it is neutral, not red or amber. The demo authenticator bar uses the same grey. Chat is the home screen: bubbles, inline cards (proposal, hold, receipt), an input dock with attach and send.

Console: dense, full-width, sticky header with product name, officer id, theme toggle and log out; a tab bar; the same strip in dark grey.

## 5. Screen list

Customer: Chat home (empty, loading, conversation with inline cards); Proposal card (typed payee, route, plan step, MFA); Hold card (document payee, with timeline); Payees (list with provenance chips, pay form, add payee); Routes (form, ranked comparison with the recommended route distinct and the saving labelled, split tip, send); Tuition plan (stepped funding timeline, totals, requests); Family (incoming, outgoing); Credit Passport (issue, credential card with selectable claims, "only this is shared" preview, share card, presentation); Verifier page (challenge, paste, result); each in English and Nepali at 380 px and desktop.

Console: Login; Verification queue (item, out-of-band form, CoP result); Proposal trace (React Flow nodes by zone told apart by border style: untrusted input dashed, model dotted, deterministic core solid; denied rules and the final denial in the red border; rule results as chips); Ledger (table, chain check pass or fail); Risk queue; Side by side (two contrasted columns, money moved highlighted); Evaluation summary.

## 6. Accessibility checklist

WCAG AA contrast for text and UI (values above); visible focus everywhere; full keyboard operation (no hover-only actions, bottom nav and sidebar are plain links); touch targets at least 44 px; `aria-current`, `role="status"` and `role="alert"` kept; `lang` follows the locale; reduced motion honoured; chips carry text, not colour alone; Devanagari uses Noto Sans Devanagari with the digit option unchanged.

## 7. Monochrome restyle (2026-10-18)
Visual only: tokens, fonts and component styling. The `redesign` screens below are the earlier green and saffron design; `docs/evidence/screens/` holds the monochrome screens.

## 8. Before and after
The current screens, English and Nepali at 380 px and the console at 1280 px, are `docs/evidence/screens/` and `docs/evidence/screens/console/`. Verification: 38 browser tests pass (selectors changed only where the layout moved: below 1024 px the secondary pages sit behind a "More" tab, so tests use `goTo()`), `check-no-external` finds no external host, strict CSP unchanged.
