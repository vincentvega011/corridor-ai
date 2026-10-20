# Corridor AI: Technical Documentation

The technical document is the PDF below:

- PDF: [`Corridor_AI_Technical_Document.pdf`](Corridor_AI_Technical_Document.pdf) (11 pages: architecture, core algorithms, security design, results and limits)
- LaTeX source: [`main_doc/`](main_doc/) (`main.tex`, figures, `refs.bib`); rebuild with `make submission-docs` (needs `tectonic`)

## Summary

Corridor AI is a synthetic cross-border and student finance assistant (Topic C). Models only produce typed proposals.
A deterministic policy lattice decides them, a grant bound to the SHA-256 of the exact canonical proposal authorises one
execution, and a single executor writes state and a hash-chained ledger inside one database transaction. The document describes
(under listed assumptions) authority separation, approval binding, at-most-once execution, taint stickiness,
ledger tamper evidence and a policy-only bound on loss for a fully deceived user.

Every component carries a status tag: **Implemented** (in code, tested), **Prototype** (in code, partial) or **Designed**
(production design, not built). Measured numbers come only from `eval/reports/20261011T175100` (a one-seed preview of the
earlier build) and the test runs recorded in the documents. No current-system benchmark of the expanded 68-task and
125-case suites is claimed; that evaluation is pending an approved planner path.

All data is synthetic. No real money moves. The ledger is hash-chained and is not a blockchain.
