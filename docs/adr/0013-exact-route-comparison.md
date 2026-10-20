# 0013. Bounded route comparison instead of a CP-SAT routing model

Date: 2026-10-12. Status: accepted (P3-1).

## Context
`docs/spec/modules.md` 5.11 specified a CP-SAT model for routing (up to two providers, a delay penalty). The P3-1
brief asks for enumeration of the small route set. The current implementation bounds wide split searches, so its
global optimality guarantee is limited as clarified in the addendum below. The two disagree on the method, not on
the goal. The spec section is amended in place.

## Decision
1. `corridor.optimizer.routes` enumerates every single channel and every pair of channels (pure Python, integer
   minor units). With four channels per corridor that is four singles and six pairs. Split ranges up to 20,000
   feasible units are scanned point by point; wider ranges scan the first and last 400 units. The wider search is
   a bounded heuristic, not a proof of the global minimum. A brute-force fixture compares one 100,000-wide AUD
   example, but that fixture does not establish optimality for all wide ranges.
2. The recommended route is the cheapest **single** channel that meets the deadline, because a recommendation
   becomes one transfer proposal with one confirmation grant (INV-5). A cheaper two channel split is shown as a
   tip, with a note that it means two separate transfers, each confirmed on its own.
3. Feasibility: per transfer limit, a new per channel daily limit (`providers.daily_limit_minor`, migration 0008),
   and ETA against the deadline counted conservatively (whole days until the deadline day starts).
4. Ranking: lowest total cost, then more delivered, fewer legs, faster, then provider id. Deterministic.
5. `compare_routes` is a READ tool (reference data, no state change). The proposal builder uses the same
   optimizer for `send_money`; the route is checked by the executor and the policy like any other. Optimizers
   never execute (INV-1), and every number shown comes from the tool result (INV-9).
6. CP-SAT stays for the tuition funding plan (P3-1 Part 3), where the choice is over amounts, not a small list.

## Consequences
The saving shown is against the most expensive feasible enumerated route, and a replay of the seeded history reports the
saving against a BankWire default (`scripts/cost_replay.py`, illustrative). Provider terms and the daily limit
(assumed 2 x the per transfer maximum) are assumptions in `docs/assumptions.md`. Not modelled: weekends and bank
holidays, the amount already sent today through a channel, provider promotions.

## Addendum (2026-10-14): guarantee clarification

The original title and wording overstated the route optimizer's guarantee. The implementation is exhaustive for
single-channel routes and pair splits whose feasible interval has at most 20,000 integer points. For wider
intervals it returns the best candidate among the first and last 400 points scanned for each pair. API and dossier
wording now describe bounded comparison. No global optimality or bounded optimality gap is claimed for wide
intervals. Establishing a global guarantee or measuring an optimality gap against a complete offline oracle
remains open work.
