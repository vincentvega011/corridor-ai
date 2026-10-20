# Assumptions

## FX rates

- **Source:** Nepal Rastra Bank (NRB) published foreign exchange rates, the public API behind https://www.nrb.org.np/forex/ : `https://www.nrb.org.np/api/forex/v1/rates` (no key needed). Fetched on 2026-10-10 by `scripts/fetch_nrb_rates.py` and saved in `data/fx/nrb_rates.json`, so seeding is offline and deterministic.
- **Dates:** the latest rate day (2026-10-09) plus one snapshot near the 10th of each month from April to September 2026 (7 dates).
- **Pairs:** NRB quotes every currency against NPR (rate is NPR per `unit` foreign units, unit 1 for AUD, QAR, MYR, CNY, USD). Mid = (buy + sell) / 2 / unit, quantised to 8 places, ROUND_HALF_EVEN, stored as `AUD/NPR`, `QAR/NPR`, `MYR/NPR`, `CNY/NPR`, `USD/NPR`.
- **Derived pairs:** `NPR/CNY` is the inverse of `CNY/NPR` (NRB has no NPR to foreign quote). The `get_fx_quote` tool derives other pairs on request: the inverse of a stored pair, or a cross through NPR (for example AUD/CNY = AUD/NPR x NPR/CNY). Every pair therefore routes via NPR, not USD, because NRB covers all five currencies directly. All pairs are indicative mid rates without provider margin.
- **Illustrative:** these are real published NRB rates used as sandbox data. They are not live, not a bank quote, and the seed history reuses the nearest earlier snapshot for past transfers.


## Provider fees

- **Everything in `data/seeds/providers.yaml` is an illustrative assumption.** The 20 providers (SwiftHome, PeakRemit, KathmanduLink, BankWire on each of the five corridors AUD to NPR, QAR to NPR, MYR to NPR, NPR to CNY, USD to NPR) are fictional. Fixed fee, percentage fee, FX margin in bps, min and max send limits and ETA hours were chosen by us to give the optimizer real trade-offs (fast and expensive, slow and cheap, high fixed fee with low margin, caps that force splitting).
- **Informed by:** the World Bank Remittance Prices Worldwide (RPW) database, https://remittanceprices.worldbank.org/ (methodology: total cost = sender fee plus exchange rate margin, as a percentage of the amount sent for a USD 200 equivalent). The corridor pages such as https://remittanceprices.worldbank.org/corridor/Qatar/Nepal show total costs in the low single digits to about 7 percent; individual provider entries seen on 2026-10-10 ranged from about 2.3 percent to 6.6 percent for Qatar and Malaysia to Nepal. We did not copy any provider's figures. We found no RPW figure for Australia to Nepal, so that corridor is purely illustrative. Total cost in the seed ranges from about 0.6 percent to 3 percent for amounts above AUD 1,000 equivalents because sandbox providers are not meant to reproduce real retail margins; small sends cost more through fixed fees.
- **Split beats single (AUD to NPR, sending AUD 5,000):** PeakRemit (max AUD 3,000, 60 bps) plus KathmanduLink (AUD 3 fixed plus 90 bps) cost AUD 39.00 in total, against AUD 60.00 for the best single provider that can carry the whole amount (BankWire, AUD 20 fixed plus 80 bps) and AUD 80.00 for SwiftHome. A test asserts these numbers.
- **Regulatory and policy limits** (daily limits, MFA thresholds) are separate configurable values in `config/policy.yaml` (phase P2).

## Seed data

- Deterministic: fixed RNG seed 20261010 in `data/seeds/users.yaml`, sandbox "today" 2026-10-10, no wall clock. Running `make seed` twice yields identical rows (tested). Only HMAC account hashes depend on the secret key in `secrets/`.
- Personas are fictional (Priya in Sydney with AUD and NPR, Ramesh in Doha with QAR and NPR, Anil in China with CNY and NPR). Balances, rent, tuition and remittance amounts are illustrative. Six months of history are generated from patterns (rent, monthly remittance, tuition, a phone bill); the balances are set explicitly and are not the result of replaying the history.
- Two payments are deliberately late (Priya rent in July, Ramesh rent in August) so the Credit Passport has real on-time ratios.
- All account numbers are fictional and derived from stable keys by `corridor.sandbox.synth`; they are never stored, only their HMAC-SHA256 and last 4 digits.
- Fixtures in `data/fixtures/` are fictional (see its README).

## Regulatory and policy limits

## Model ids and endpoints

Verified 2026-10-10 against the live `GET /models` of the Singapore (international) workspace and the official docs.
- Endpoint form: `https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1` (Model Studio function calling page,
  https://www.alibabacloud.com/help/en/model-studio/qwen-function-calling). The `/models` call returned 99 ids.
- Chosen: planner `qwen3.8-max`; composer and parser_fallback `qwen3.8-flash`; eval comparison set `qwen3.8-max`, `qwen3.7-plus`, `qwen3.8-flash`.
- Function calling: the Qwen-Max series (includes Qwen3.8-Max) and Qwen-Plus series (includes Qwen3.7-Plus) are listed. Qwen3.8-Flash is
  NOT listed for function calling, so composer and parser_fallback use no tools (https://www.alibabacloud.com/help/en/model-studio/qwen-function-calling).
- Structured output (https://www.alibabacloud.com/help/en/model-studio/json-mode): `json_object` is listed for Qwen3.8-Max, Qwen3.8-Flash and Qwen3.7-Plus.
  `json_schema` is listed for the same models but the page says Singapore region models are not supported yet, so the gateway uses `json_object`.
- Thinking mode: `tool_choice` cannot be `required` or a specific function with `enable_thinking=true`. The gateway sends `enable_thinking=false`.
- Account status on 2026-10-10: every model call returned 403 `AccessDenied.Unpurchased` (the account must activate pay-as-you-go or
  the model service in the console). No live cloud call has succeeded yet.
- Local: parser base `mlx-community/Qwen3-1.7B-4bit` (0.98 GB), local planner `mlx-community/Qwen3-4B-4bit` (2.28 GB),
  reader Ollama `qwen3-vl:4b-instruct-q4_K_M` (3.3 GB). `mlx_lm.server` serves its model under `default_model`; any other name makes it try to download that repo.

## Cloud provider: Token Harbor (default since 2026-10-10)

Alibaba Model Studio is paid only for the models we need (every call returned 403 AccessDenied.Unpurchased), so the default cloud provider is Token Harbor, an OpenAI-compatible gateway with free `:free` routes. Alibaba stays in `models.yaml` but disabled.
- Endpoint: base URL `https://tokenharbor.ai/v1`, `POST /v1/chat/completions`, `GET /v1/models`, `Authorization: Bearer <key>` (https://tokenharbor.ai/docs/api/curl, read 2026-10-10).
- Free account limits: 60 requests/min and 1,800/hour per account, shared across all API keys and models; also 100/min and 3,000/hour per IP; free promotional models have no extra per-model limit. Exceeding a limit returns 429 with `Retry-After`; no other rate-limit headers are documented (https://tokenharbor.ai/docs/api/rate-limits, read 2026-10-10). Our client-side limits are 50/min and 1,500/hour (assumption: a safety margin of about 17 percent).
- Data policy: free models are off by default; if enabled, "Token Harbor may retain free-route prompts and responses" for diagnostics, safety, optimization and improvement; consent is not extended to newly added free models; paid routes are not persistently stored (https://tokenharbor.ai/privacy, read 2026-10-10). We send synthetic data only.
- Not documented, so tested live (status below): tool calling, `response_format`, presence of the `usage` object, and the exact id of DeepSeek V4.1 Flash free (expected `deepseek-v4.1-flash:free`, possibly prefixed). The docs say `GET /v1/models` lists the ids.
- Account status: the API returned 403 `email_verification_required` until the account email was verified; after verification everything below was tested live on 2026-10-10.
- `GET /v1/models` returned 68 ids (5 `:free`): claude-haiku-5.5:free, deepseek-v4-flash:free, deepseek-v4.1-flash:free, mimo-v2.5:free, mimo-v2.6-flash:free. Each entry has `tool_call` and `function_call` booleans (all true for the free models) and a `pricing` object (0 for the free ones). Free `:free` ids are separate catalogue entries from the paid ones.
- Live results through the gateway (all five free models passed plain completion, tool calling, JSON object mode and schema-in-prompt JSON, and returned a `usage` object):
  - deepseek-v4.1-flash:free (planner): plain 2.4 s; single tool call ok; two tools in one turn ok (both calls returned); json_object ok; prompt JSON ok; 5 calls p50 2.8 s, max 3.6 s.
  - claude-haiku-5.5:free (composer, parser_fallback): plain 2.1 s, tool ok, json_object 2.6 s, prompt JSON 3.0 s. Fastest of the five.
  - deepseek-v4-flash:free: all ok but json_object took 29 s once. mimo-v2.5:free 7 to 9 s per call. mimo-v2.6-flash:free 4.6 to 5.9 s.
- Role assignment (revised 2026-10-10 to keep the stack open-weight): planner, composer and parser_fallback all use deepseek-v4.1-flash:free; claude-haiku-5.5:free stays only in the eval comparison set (cross-model-family check) with local Qwen3 4B (mlx). Limits are per account, so spreading roles across models does not add quota; it only diversifies behaviour.
- Free-route latency is 2 to 9 s per call, so the local parser (about 0.5 to 1 s) stays the first stage of the NLU cascade.
- Local tool calling (verified 2026-10-10): Qwen3 4B 4-bit on `mlx_lm.server` returned the expected tool call 3 of 3 times, so `local_planner` supports tools.

## Open-weight status of the cloud model

DeepSeek V4.1 Flash weights are openly published: the official Hugging Face model card https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash (read 2026-10-10) declares licence MIT ("This repository and the model weights are licensed under the MIT License"), lists safetensors weights, a 552B-parameter backbone (8B active per token in prefill, 16B in decode) and 763B parameters in the safetensors metadata (the page does not explain the difference). Caveat for the pitch line "runs on open-weight models a bank can self-host": the weights are open, but a model of this size needs a multi-GPU server; a bank would run it on contracted or in-house inference infrastructure, or swap in a smaller open model (the local Qwen3 4B path already works) behind the same gateway interface. We have not self-hosted DeepSeek V4.1 Flash ourselves; the free route we use is served by Token Harbor.

## Model prices (alibaba, disabled)

Singapore list prices per 1M tokens, no cache or batch discount, read 2026-10-10 from
https://www.alibabacloud.com/help/en/model-studio/model-pricing (page updated 2026-10-09): qwen3.8-max 2 USD in, 6 USD out;
qwen3.8-flash 0.15 in, 0.47 out; qwen3.7-plus 0.4 in, 1.6 out up to 256K input per request (1.2 and 4.8 above; marked "limited-time 20% off").
Stored as integer micro-USD in `backend/config/models.yaml`. Alibaba reprices often; re-check before the final eval.

## Nepali festival dates (data/calendar/festivals.yaml)

Retrieved 2026-10-10. Each date in the file has a source key and a `verified` flag; a date with no authoritative source is `null` and `verified: false`, and the NLU returns "date unknown" instead of guessing.

| Festival | 2026 | 2027 | Source |
|---|---|---|---|
| Dashain, Ghatasthapana (start, used for "before Dashain") | 2026-10-11 | 2027-09-30 | 2026: Hamro Patro Nepali public holidays 2083 BS, https://www.hamropatro.com/nepali-public-holidays/2083 . 2027: Drik Panchang Nepali Dashain calendar, https://www.drikpanchang.com/nepali/dashain/dashain-calendar.html?year=2027 (computed panchang, the 2084 BS government list is not yet published) |
| Dashain, Vijaya Dashami (tika day, "for Dashain") | 2026-10-21 | 2027-10-10 | same. Calendarific lists 2026-10-20 (https://calendarific.com/holidays/2026/np); Hamro Patro, which follows the Nepali calendar, lists 2026-10-21 and is used |
| Tihar, Laxmi Puja | 2026-11-08 | 2027-10-29 | 2026: Hamro Patro 2083 (Calendarific agrees). 2027: Drik Panchang Nepali Tihar calendar, https://www.drikpanchang.com/nepali/tihar/tihar-calendar.html?year=2027 |
| Bhai Tika | 2026-11-11 | 2027-10-31 | 2026: Hamro Patro 2083. 2027: Drik Panchang (same page) |
| Chhath | 2026-11-15 | unverified (null) | 2026: Hamro Patro 2083, Calendarific agrees. 2027: no authoritative source found |
| Lhosar | Tamu 2026-12-30 | Sonam 2027-02-07 | Hamro Patro 2083 (also lists Gyalpo Lhosar 2027-03-09). Tamu Lhosar 2027-12-30 is only on third party sites and is not used |
| Nepali New Year (Baisakh 1) | 2026-04-14 (2083 BS) | 2027-04-14 (2084 BS) | 2026: Hamro Patro 2083, The Himalayan Times. 2027: Vedic Marga panchang for Baishakh 1 2084 BS, https://vedicmarga.com/hindu-calendar/2027/04/14/ ; the Hamro Patro 2084 holiday list is not published yet |

Notes (revised 2026-10-10): "before Dashain" and "Dashain aghi" mean on or before Vijaya Dashami (the Tika day); "before Dashain starts" and "ghatasthapana aghi" mean Ghatasthapana. The same main day rule applies to Tihar (Laxmi Puja) and the others. No "day before" arithmetic is used. We did not use a Bikram Sambat conversion library; dates are looked up, not computed. The 2027 festival dates come from computed panchang pages and should be re-checked against the Nepal Gazette holiday list for 2084 BS when it is published.

## Number words (data/lang/ne/numbers.yaml)

Nepali number words 0 to 100 were compiled from the Wikipedia article https://en.wikipedia.org/wiki/Numbers_in_Nepali_language and common usage (retrieved 2026-10-10). Several romanisations are listed per value, and `romanisation_folding.yaml` collapses vowel length and doubled consonants. The irregular forms between 11 and 99 were NOT individually verified against a dictionary; the eval labels contain a few of them. A test asserts that no two different number words fold to the same key (this caught a real collision between "teen" and "ten").

## DeepSeek reasoning on Token Harbor (measured 2026-10-10)

`deepseek-v4.1-flash:free` returns hidden reasoning tokens by default. For a 4 message generation request it produced about 830 completion tokens and took 12 s; with the request field `thinking: {type: disabled}` it produced 93 tokens in 3.7 s (`enable_thinking: false` behaved the same, `reasoning_effort` and `reasoning` objects did not reduce it). The composer and parser_fallback roles therefore send `thinking: {type: disabled}` (models.yaml `extra_body`). The planner role also sends `thinking: {type: disabled}` since P1-2 (hidden reasoning caused 18 s calls and timeouts on a short JSON task). Whether reasoning would help planning quality is an eval question (docs/spec/evaluation-spec.md 8.3). Batch jobs can raise the per call output budget and deadline with `complete(..., max_tokens=, timeout_s=)` (bounded at 8192 tokens and 180 s); these overrides are for trusted code only, never for model or user input.

## Policy limits (backend/config/policy.yaml, added 2026-10-11)

All limits are illustrative engineering assumptions for the sandbox, not legal limits or any institution's
real limits, and are configurable. They are expressed in NPR equivalents and converted with the FX snapshot
inside `AccountState` (the NRB based rates seeded in `data/fx`, see the FX section above), rounded up.

| Value | Setting | Why this value |
|---|---|---|
| Low tier | NPR 25,000.00, verified payees older than 30 days only (changed from NPR 10,000 and 7 days on 2026-10-11) | a routine family remittance should be confirm-only so the demo can show the normal path next to a blocked attack; new or recently added payees keep the stricter MFA path |
| Daily outgoing limit | NPR 1,000,000.00 | high enough that one tuition invoice (about AUD 6,500, roughly NPR 570,000 at the seeded rate) can be paid, low enough to be a meaningful cap on a stolen session |
| New payee age | 30 days | see the low tier; docs/spec/modules.md 5.7 says 7 days, the stricter value is used |
| Velocity | MFA above 3, DENY above 6 transfers in 10 minutes | a normal user sends a few transfers a month |
| Risk thresholds | 6000 and 9000 bps | placeholders until the risk model exists (P3), then calibrated on the synthetic held out set |
| Safety lock | 3 denied or failed high risk attempts, or 5 failed TOTP codes, in 10 minutes | INV-11 (docs/spec/architecture.md) and docs/spec/modules.md 5.8 |
| TOTP | RFC 6238, 30 s step, accepts one step either side, each step usable once | standard authenticator behaviour |

## Reader dependencies and limits (2026-10-11)

- **pypdfium2 5.14.0** rasterises PDFs. Package metadata: licence BSD-3-Clause and Apache-2.0 (the bundled
  PDFium binary carries BSD-3-Clause and Apache-2.0 plus its dependency licences). Pillow 12.3.0 (MIT-CMU)
  re-encodes images. Both are permissive and have no network access in our use.
- **Reader model:** Qwen3-VL 4B instruct, 4 bit, through Ollama. On the M4 Air a document takes about 7 to
  15 s after the first load (the role timeout is 120 s because first load and image encoding are slow).
  Accuracy on real world documents is not measured; the fixtures are clean synthetic renderings.
- **Reader cassettes** in `eval/cassettes/reader` contain the synthetic account numbers printed on the
  fixtures and are exempt from the record-time leak scan (ADR 0008). Never record them from real documents.
- **Grounding** treats small bare integers (below 100, no unit) as not being claims about money; see ADR 0009.

## Payee cooling-off (P2-4, `backend/config/policy.yaml`)

| Value | Setting | Basis |
|---|---|---|
| `payee_cooloff.hours` | 24 | Illustrative. Many banks delay or limit the first payments to a newly added payee for a day or more; scammers depend on speed, so a one day wait gives a victim, a relative or the bank time to notice. Not a legal requirement, and not a statement about any bank's rules. |
| `payee_cooloff.first_payment_cap_npr_minor` | 500000 (NPR 5,000.00 equivalent, in total per destination account) | Illustrative. Small enough to limit loss on a scam, large enough for a small genuine first payment. Compared with the NPR equivalent from the FX snapshot, rounded up. |
| `payee_cooloff.untrusted_origin_cap_npr_minor` | 0 (a full hold for 24 hours) | Illustrative. An account that came from a document, a message or a model guess is the kind scammers supply, and the user did not choose it knowingly, so nothing is paid to it until the cooling-off ends. A real recipient can wait one day; a scammer's account cannot be told apart in time. Applies if any stored row with the same account hash has untrusted or model-inferred provenance. |
| After the hold (P2-7) | an unverified account of untrusted origin keeps MFA with the warning and a rolling 24 hour cap equal to `first_payment_cap_npr_minor` (NPR 5,000) until it is verified | Illustrative. Time alone must not turn an attacker supplied account into a trusted one (INV-4). The cap reuses the typed account cap so no new number is invented. Verification is a settings action that does not exist yet. |

## Deadline calendar (P3-1)

| Item | Value | Note |
|---|---|---|
| Harbourview University semester starts | 2026-07-27, 2027-02-22, 2027-07-26 | Invented for the sandbox (fictional university), file `data/calendar/university.yaml`, 2026-10-12. Not real data |
| "next Friday" | the Friday of next Monday to Sunday week | convention, `deadline_holdout.jsonl` |
| Deadline time | end of the deadline day (23:59:59) in the user's time zone | convention |

## Route comparison (P3-1)

- **CNY to NPR channels:** four more fictional channels (PeakRemit, KathmanduLink, SwiftHome, BankWire), same shape as the other corridors (fixed fee, percentage fee, FX margin over the NRB mid rate in the seed, ETA in hours, per transfer limits). All values are invented for the sandbox (`data/seeds/providers.yaml`, 2026-10-12), informed only by the shape of World Bank Remittance Prices Worldwide data (https://remittanceprices.worldbank.org/). Not quotes.
- **Daily limit per channel:** assumed to be 2 x the per transfer maximum (`daily` in providers.yaml, migration 0008). Invented; real providers publish their own limits.
- **Deadline feasibility:** a route is on time if its ETA hours fit in the whole days until the deadline day starts. Weekends, bank holidays and cut-off times are not modelled.
- **Cost replay (`make cost-replay`), ILLUSTRATIVE:** prices the 9 seeded remittance transfers (12 months, AUD and QAR) with BankWire as the naive default against the cheapest single channel. ILLUSTRATIVE: invented terms, seeded history.

## Tuition funding (P3-1)

- **Spending buffer:** after funding a fee the spending account keeps at least AUD 500.00 (CNY 2,000, QAR 200, MYR 200, USD 300, NPR 15,000), `config/funding.yaml`. A planning preference, invented, 2026-10-12.
- **Priya's accounts:** AUD 4,200.00 spending, AUD 1,500.00 savings, NPR 620,000.00; fee AUD 6,500.00 due 2026-10-31. All synthetic (`data/seeds/users.yaml`).
- **Family link:** Ramesh (her uncle in Qatar) can be asked for up to QAR 5,000.00 per request. Invented; the limit is set by the bank, not by a model.
- **QAR to AUD channels:** PeakRemit, SwiftHome and BankWire, invented fees, limits and ETAs (`data/seeds/providers.yaml`, 2026-10-12). The QAR/AUD mid rate is the cross through NPR of the NRB sandbox rates.
- **Due date:** counted in whole days from today (conservative); the family money is assumed to arrive after the channel's ETA once the contributor agrees.

## Risk score weights (P3-1)

The weights, thresholds, the 3x and 10x amount multiples and the small hours window (00:00 to 05:00 local) in `backend/config/policy.yaml` `risk:` are illustrative assumptions chosen by us (2026-10-12), not fitted to data: there is no real fraud data and the synthetic history is too small to tune on. The score is a transparent rule list for explanation and as an extra brake, not a claim of fraud detection accuracy.

## Contribution requests and passport (P3-1b)

- **Contribution request cap:** at most 3 requests per contributor per rolling 24 hours (`contribution.max_requests_per_contributor_per_day` in `backend/config/policy.yaml`, rule `R-CONTRIBUTION`, reason `contribution_daily_cap`). Invented; it stops one person pressing the same relative, it is not a legal limit. Counted from the database, per requester and contributor. Test: `tests/integration/test_contribution_cap.py`.
- **Passport validity and limits:** 90 days, no revocation list, holder key kept server side in the sandbox (on the customer's device in a real product). All invented for the prototype (ADR 0015).
- **Time zones:** the small hours window and local dates use IANA zones through `zoneinfo`; the `tzdata` package is a declared dependency so the offline demo does not rely on the operating system's zone database.

## Festival dates and the demo clock (P3-2)

**Demo clock.** `make demo` fixes the sandbox date to 10 Oct 2026, 09:00 Nepal time (`DEMO_CLOCK=on`, `corridor.clock`). Reason: Vijaya Dashami 2026 is 21 Oct, so "before Dashain" has an 11 day deadline and the seeded history (as of 10 Oct) is current. Dev and demo only; the apps show a banner while it is on.

**Sources for `data/calendar/festivals.yaml`** (retrieved 2026-10-10, checked again 2026-10-12). Every date has a recorded source. None of them is the Nepal Gazette itself: it was not opened. The official list for 2083 B.S. is the Ministry of Home Affairs notice of 2082/11/18 (2 Mar 2026), reported with its Dashain, Tihar and Chhath ranges by [Pradhan Law](https://pradhanlaw.com/publications/list-of-public-holidays-2083-202627), the [Embassy of Nepal list](https://my.nepalembassy.gov.np/pages/public-holidays--2083---2026-27-/) and [Educate Nepal](https://blog.educatenepal.com/2026/03/list-of-public-holidays-for-2083-bs.html). That notice gives holiday ranges (Dashain 17 to 23 Oct 2026, Tihar 8 to 12 Nov 2026, Chhath 15 Nov 2026), not main days.

| Date used | Festival | Source recorded in the file | Grade | Cross-check with the Ministry of Home Affairs ranges |
|---|---|---|---|---|
| 2026-10-11 | Ghatasthapana | [Hamro Patro 2083](https://www.hamropatro.com/nepali-public-holidays/2083) | official calendar site | Not a separate holiday in the range; Dashain range starts 17 Oct |
| 2026-10-21 | Vijaya Dashami | Hamro Patro 2083 (Calendarific lists 20 Oct) | official calendar site | Inside the Dashain range 17 to 23 Oct |
| 2026-11-08 | Laxmi Puja | Hamro Patro 2083 | official calendar site | Inside the Tihar range 8 to 12 Nov |
| 2026-11-11 | Bhai Tika | Hamro Patro 2083 | official calendar site | Inside the Tihar range 8 to 12 Nov |
| 2026-11-15 | Chhath | Hamro Patro 2083 (Calendarific agrees) | official calendar site | Equals the listed Chhath holiday |
| 2026-12-30 | Tamu Lhosar | Hamro Patro 2083 | official calendar site | Not cross-checked |
| 2027-02-07 | Sonam Lhosar | Hamro Patro 2083 | official calendar site | Not cross-checked |
| 2026-04-14, 2027-04-14 | Baisakh 1 (new year) | Hamro Patro 2083; [Vedic Marga](https://vedicmarga.com/hindu-calendar/2027/04/14/) for 2084 | official site / computed | 2083 starts 14 Apr 2026 per the notice; 2084 list not yet published |
| 2027-10-10, 2027-09-30, 2027-10-29, 2027-10-31 | Dashain, Ghatasthapana, Laxmi Puja, Bhai Tika 2027 | [Drik Panchang Dashain](https://www.drikpanchang.com/nepali/dashain/dashain-calendar.html?year=2027), [Tihar](https://www.drikpanchang.com/nepali/tihar/tihar-calendar.html?year=2027) | computed panchang | The 2084 list is not published yet; re-check before use |

Not verifiable from an official source today: any 2027 date (computed by a third party), the day within a range on which a festival's main day falls (the notice lists ranges), and Chhath 2027 (no source, so the agent asks). The semester calendar in `data/calendar/university.yaml` is an invented example. Re-check every row against the Gazette before any real use.


## Anil lives in Shenzhen (P4-1)

- Anil Gurung is a fictional Nepali student at a fictional institute (Northridge Institute) in Shenzhen, `home_region` CN, time zone Asia/Shanghai, CNY account (CNY 21,500.00) and NPR account. His tuition (CNY 18,000.00 due 15 Nov 2026) and rent are paid in CNY. The earlier app label "Kuala Lumpur" was wrong and is removed; MYR stays a supported currency only (Malaysia is not a persona region).
- **No China specific remittance limit is configured.** `backend/config/policy.yaml` has one set of limits for every region (illustrative, NPR 25,000 low tier, NPR 1,000,000 daily). If a China outward or inward limit is added later it is a configurable policy value, an illustrative assumption with a source and date, never a legal statement.
- Rates for CNY come from the same seeded NRB table as every other pair: **CNY/NPR 23.105 (NRB reference rate, 2026-10-09, illustrative)**.
- Exchange rates in use everywhere (routes screen, tuition plan, composer, reports, red-team thresholds): one table, `fx_mid_rates`, latest date 2026-10-09 seeded from `data/fx/nrb_rates.json` (buy/sell mid): AUD/NPR 107.65, QAR/NPR 42.485, CNY/NPR 23.105, MYR/NPR 37.85, USD/NPR 154.86; NPR/CNY is the inverse. The cost replay uses the same table by date (history from April to October). Earlier comments in `eval/` assuming AUD about 104.5, QAR 40.7 and CNY 21.7 came from the first day of the file (10 Apr 2026), not from the rate in use; no expected outcome depends on the difference (checked: every threshold case is on the same side of NPR 25,000 and 5,000 at the rates in use).
