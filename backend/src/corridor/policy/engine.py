"""The policy engine: a pure function from (proposal, state, config, now) to a decision (INV-2).

No I/O, no clock, no randomness, no LLM, no network. The same input always gives the same decision.
Rules run independently and the decision is the most restrictive of them (INV-7). An unknown action
is denied (INV-10). The policy only ever says ``ALLOW`` for read tools, never for an action.
"""

from collections.abc import Sequence
from datetime import datetime

from corridor.contracts.action import ActionProposal
from corridor.contracts.policy import Decision, PolicyDecision, combine
from corridor.policy.config import PolicyConfig
from corridor.policy.rules import KNOWN_ACTIONS, RULES, Rule, RuleResult
from corridor.policy.state import AccountState


def _decide(results: list[RuleResult], version_hash: str) -> PolicyDecision:
    final = combine(r.decision for r in results)
    ordered = sorted(results, key=lambda r: (-r.decision.rank, r.rule_id, r.reason))
    rule_ids: list[str] = []
    for r in ordered:
        if r.rule_id not in rule_ids:
            rule_ids.append(r.rule_id)
    shown = (
        [r for r in ordered if r.decision is Decision.DENY]
        if final is Decision.DENY
        else [r for r in ordered if r.rule_id != "R-FLOOR"] or ordered
    )
    reasons = tuple(dict.fromkeys(f"{r.rule_id}.{r.reason}" for r in shown))
    return PolicyDecision(
        decision=final,
        rule_ids=tuple(rule_ids),
        reasons=reasons,
        rule_results=tuple((r.rule_id, r.decision.value, r.reason) for r in ordered),
        policy_version_hash=version_hash,
    )


def evaluate(
    proposal: ActionProposal,
    state: AccountState,
    config: PolicyConfig,
    now: datetime,
    extra_rules: Sequence[Rule] = (),
) -> PolicyDecision:
    """Decide what an ``ActionProposal`` needs before it may run.

    ``extra_rules`` exists so tests can prove monotonicity: adding a rule never makes the decision
    less restrictive. Production code never passes it.
    """
    if proposal.action not in KNOWN_ACTIONS:
        return _decide(
            [RuleResult("R-UNKNOWN", Decision.DENY, "unknown_action")], config.version_hash
        )
    results: list[RuleResult] = []
    for rule in (*RULES, *extra_rules):
        results.extend(rule(proposal, state, config, now))
    return _decide(results, config.version_hash)


def evaluate_read(tool: str, config: PolicyConfig) -> PolicyDecision:
    """R-TIER-READ: a registered read only tool is allowed; anything else is denied (INV-1)."""
    if tool in config.read_tools:
        results = [RuleResult("R-TIER-READ", Decision.ALLOW, "read_only")]
    else:
        results = [RuleResult("R-TIER-READ", Decision.DENY, "unknown_tool")]
    return _decide(results, config.version_hash)
