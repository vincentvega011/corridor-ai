<!-- Product thesis (section 1). Section numbers kept so old references still resolve. -->

## 1. Product thesis

### One-line pitch
**"The AI can be fooled. The money still can't move."**

Student-first pitch: **"A safe AI finance assistant for international students, and the families and workers who move money across borders with them."** Priya, the international student, is the lead persona of every demo and document; Ramesh (a migrant worker sending money home) and Anil (a student in China) show that the same safety core serves the whole family corridor.

Supporting line: **"runs on open-weight models a bank can self-host."** The planner, parser, reader and composer are all open-weight model families (DeepSeek, Qwen) reached through an OpenAI-compatible interface, so a bank can run the same code against its own hosted endpoint.

Corridor AI is a safety-constrained multi-agent system that lets international students and migrant workers manage money across Asia-Pacific corridors in plain, code-mixed language. The LLM interprets, plans and proposes. Deterministic software decides whether anything is allowed to happen, every value is tracked for where it came from, and every decision is written to a tamper-evident ledger.

### What Corridor AI can do that an ordinary finance agent cannot
Complete a multilingual, multi-step cross-border payment workflow **and prove why every consequential financial action was permitted, or why it was stopped**, even when the model itself has been manipulated.

### Users and corridors
- **Priya (lead persona)**, a Nepali international student in Sydney: her parents fund tuition from Nepal in NPR, she pays the university invoice and rent in AUD, and wants to build credit history abroad. Invoice redirection (a fake "updated bank details" invoice) is a real scam against international students.
- **Ramesh**, a Nepali worker in Doha: sends QAR home every month, gets scam messages, writes in romanised Nepali.
- **Anil**, a Nepali student at a fictional institute in Shenzhen (region CN): pays CNY tuition and rent from a CNY account; family money arrives on the NPR to CNY corridor and he can send CNY home on the CNY to NPR corridor. The apps label him "Anil (student, Shenzhen)". Hong Kong, Macao and Taiwan are regions of China in every region list; MYR is only a currency option.
- Pilot corridors: **AUD to NPR, QAR to NPR, MYR to NPR, NPR to CNY, USD to NPR**. The system is corridor-agnostic; corridors are config.

### Four scenarios (all named in Topic C)
1. **Remittance**: send home at the lowest total cost, scheduled or one-off, with scam protection.
2. **Tuition payment**: read a university invoice, verify it, fund it from several sources, pay before the due date.
3. **Multi-currency management**: balances, budgets and alerts across NPR, AUD, QAR, MYR, CNY, USD.
4. **Credit building**: turn on-time rent, tuition and remittance history into a signed, portable, verifiable credential (Credit Passport).

### Pitch order (how the judges must perceive the project)
| Priority | Modules | Message |
|---|---|---|
| 1. Core innovation | Quarantined reader, taint tracking, policy engine, WYSIWYS confirmation grants | "Safe even when the model is fooled" |
| 2. Useful financial agent | Code-mixed NLU (our own fine-tuned model), planner DAG, sandbox bank, routing optimizer | "It actually gets real cross-border tasks done" |
| 3. Verifiable trust | Grounding verifier, hash-chained audit ledger, bank operations console | "A bank can supervise it" |
| 4. Supporting capabilities | Scam risk model, Credit Passport, evaluation harness | "Measured, explained, extensible" |

The evaluation harness is presented as **major evidence**, not a footnote. Every claim in the documents maps to a number from `eval/reports/`.
