"""Deterministic risk score with reason codes (P3-1, ADR 0016, modules.md 5.12 as amended).

No model and no randomness: a handful of facts about the proposal and the user's own state add fixed
weights (basis points, ``config/policy.yaml``). The score is an input to policy only through
``R-RISK``,
which can ask for MFA or deny but never relax anything (INV-7), and ``assess`` is a pure function of
``(proposal, state, config, now)`` (INV-2). The reason codes are what the console and the user see.
"""

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from corridor.contracts.action import ActionProposal, ActionType
from corridor.contracts.provenance import Provenance
from corridor.contracts.risk import RiskAssessment, RiskLevel, RiskReason
from corridor.policy.config import PolicyConfig
from corridor.policy.state import AccountState

MODEL_ID = "rules-v1"
TAINTED = frozenset({Provenance.UNTRUSTED, Provenance.MODEL_INFERRED})
SCORED = frozenset(
    {
        ActionType.TRANSFER, ActionType.PAY_INVOICE, ActionType.CONVERT, ActionType.SCHEDULE,
        ActionType.ADD_PAYEE,
    }
)  # fmt: skip
REASON_TEXT = {
    "new_payee": "The payee was added recently.",
    "untrusted_payee": "The payee's details came from a document or message and are not verified.",
    "held_payee": "The payee is still in its 24 hour cooling off period.",
    "document_derived": "The request is based on a document or message.",
    "amount_unusual": "The amount is much larger than your usual payments.",
    "amount_extreme": "The amount is far larger than your usual payments.",
    "unusual_hour": "The payment is requested in the small hours of your local time.",
    "recent_denials": "Recent requests on your account were refused.",
    "cop_mismatch": "The payee name does not match the registered account holder.",
}


def _local_hour(now: datetime, zone: str) -> int:
    try:
        return now.astimezone(ZoneInfo(zone)).hour
    except (ZoneInfoNotFoundError, ValueError):
        return now.astimezone(ZoneInfo("UTC")).hour


def contributions(
    p: ActionProposal, s: AccountState, c: PolicyConfig, now: datetime
) -> list[tuple[str, int]]:
    """Every reason that applies, with the basis points it adds (unsorted, uncapped)."""
    if p.action not in SCORED:
        return []
    w = c.risk.weights_bps
    out: list[tuple[str, int]] = []
    payee = s.payees.get(p.payee_id.value) if p.payee_id is not None else None
    carried_taint = p.payee_id is not None and p.payee_id.provenance in TAINTED
    if payee is not None:
        age = now - payee.created_at
        if age.days < c.new_payee_days:
            out.append(("new_payee", w.get("new_payee", 0)))
        if payee.provenance in TAINTED and not payee.verified:
            out.append(("untrusted_payee", w.get("untrusted_payee", 0)))
            if age.total_seconds() < c.payee_cooloff.hours * 3600:
                out.append(("held_payee", w.get("held_payee", 0)))
        if payee.cop == "mismatch" and not payee.verified:  # an officer verifies only on a match
            out.append(("cop_mismatch", w.get("cop_mismatch", 0)))
    elif carried_taint or p.action is ActionType.ADD_PAYEE:
        if carried_taint or (
            p.new_payee is not None
            and any(
                f.provenance in TAINTED for f in (p.new_payee.display_name, p.new_payee.account_no)
            )
        ):
            out.append(("untrusted_payee", w.get("untrusted_payee", 0)))
    if p.derived_from_docs:
        out.append(("document_derived", w.get("document_derived", 0)))
    if p.amount is not None and s.history_median_npr_minor > 0:
        rate = s.fx_to_npr.get(p.amount.value.currency)
        if rate is not None:
            npr = int(rate * p.amount.value.amount_minor)
            if npr > c.risk.amount_extreme_multiple * s.history_median_npr_minor:
                out.append(("amount_extreme", w.get("amount_extreme", 0)))
            elif npr > c.risk.amount_unusual_multiple * s.history_median_npr_minor:
                out.append(("amount_unusual", w.get("amount_unusual", 0)))
    start, end = c.risk.quiet_hours
    if start <= _local_hour(now, s.timezone) < end:
        out.append(("unusual_hour", w.get("unusual_hour", 0)))
    if s.recent_denials > 0:
        out.append(
            (
                "recent_denials",
                min(
                    s.recent_denials * w.get("recent_denial_each", 0),
                    w.get("recent_denials_max", 0),
                ),
            )
        )
    return [(code, bps) for code, bps in out if bps > 0]


def risk_view(a: RiskAssessment) -> dict[str, object]:
    """The assessment as plain JSON for events and the ledger (reason codes with their weights)."""
    return {
        "score_bps": a.score_bps, "level": a.level.value, "model_id": a.model_id,
        "reasons": [
            {"code": r.feature, "text": r.text, "contribution_bps": r.contribution_bps}
            for r in a.top_reasons
        ],
    }  # fmt: skip


def assess(p: ActionProposal, s: AccountState, c: PolicyConfig, now: datetime) -> RiskAssessment:
    """Score (0 to 10000), level and the three biggest reasons for a proposal."""
    parts = contributions(p, s, c, now)
    score = min(sum(b for _, b in parts), 10000)
    level = (
        RiskLevel.CRITICAL
        if score >= c.risk.critical_bps
        else RiskLevel.HIGH
        if score >= c.risk.high_bps
        else RiskLevel.LOW
    )
    top = sorted(parts, key=lambda cb: (-cb[1], cb[0]))[:3]
    return RiskAssessment(
        score_bps=score, level=level, model_id=MODEL_ID,
        top_reasons=tuple(
            RiskReason(feature=code, text=REASON_TEXT[code], contribution_bps=bps)
            for code, bps in top
        ),
    )  # fmt: skip
