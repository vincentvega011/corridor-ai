<!-- Machine-assisted translation, pending native review. Simplified Chinese (zh-CN), mainland banking terms. -->
# Glossary: Simplified Chinese (zh-CN) UI

Status: **machine-assisted translation, pending native review.** One term per concept, used everywhere in `frontend/messages/zh.json`, `frontend/messages/console.zh.json` and `frontend/messages/server.zh.json`. Display language only: chat understanding (parser, planner) stays English and Nepali.

| English | 简体中文 | Note |
|---|---|---|
| sandbox | 沙盒 | "沙盒环境：无真实资金" |
| payee / recipient | 收款人 | account holder 账户持有人 |
| send money, transfer | 转账 | domestic or generic; the verb for a button is 汇款 when it crosses borders |
| remittance, cross-border send | 汇款 | "汇款路线", "汇款金额" |
| authenticator code (one-time code) | 验证码 | the app is the "验证器" |
| tuition | 学费 | |
| invoice | 账单 | |
| risk | 风险 | "风险队列" |
| ledger | 账本 | hash-chained, tamper-evident; never "区块链" (it is not a blockchain) |
| hold (payee hold) | 暂缓 | "暂缓中" chip; "冻结" is not used (it implies a legal freeze) |
| proposal | 提案 | "提案指纹" for the fingerprint |
| confirm / cancel | 确认 / 取消 | |
| policy / rule | 策略 / 规则 | rule ids (R-...) stay Latin |
| denied | 拒绝 | outcome badge |
| allowed | 允许 | |
| needs confirmation | 需要确认 | |
| fees | 手续费 | total cost 总费用 |
| exchange rate | 汇率 | |
| arrives | 到账 | |
| Credit Passport | 信用护照 | claim 声明, verifier 验证方, challenge 挑战码 |
| bank officer | 银行工作人员 | console: 柜员 is not used |
| verification (payee) | 核实 | out-of-band check 带外核实 |
| Confirmation of Payee (CoP) | 收款人姓名核验 (CoP) | |
| untrusted | 不可信 | "来自文件（不可信）" |
| scam warning | 诈骗风险提示 | |
| trace | 追踪 | "提案追踪" |
| deterministic core | 确定性核心 | zones: 客户输入 / 不可信输入 / 模型 / 确定性核心 |
| region | 地区 | Hong Kong, Macao and Taiwan are regions of China: always "地区", never "国家" |

Kept unchanged: personas and proper names (Priya, Ramesh, Anil, CityNest Bookings, Wattle Lane Property), provider names (PeakRemit, KathmanduLink, SwiftHome, BankWire), currency codes (AUD, NPR, QAR, CNY, MYR, USD), masked account numbers, rule ids, hashes.

Formats: dates "2026年10月11日 14:15" with the zone named (NPT, AEDT, GMT+8); numbers with standard grouping; money "AUD 2,000.00" (code before the amount, as in English). The Devanagari digit option is hidden in zh-CN.
