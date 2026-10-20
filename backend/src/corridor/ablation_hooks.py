"""The ablation hooks used by the graph and the executor (ADR 0010).

Kept outside ``corridor.policy`` so the policy package stays a pure function (INV-2). With no ablation
active every function here is the identity or calls the real engine."""

from datetime import datetime, timedelta

from corridor.ablation import Ablation, Ablations, strip_taint
from corridor.contracts.action import ActionProposal
from corridor.contracts.confirm import ConfirmationGrant, ConfirmationLevel
from corridor.contracts.policy import Decision, PolicyDecision
from corridor.contracts.provenance import Provenance
from corridor.policy import AccountState, evaluate
from corridor.policy.config import PolicyConfig

FLOOR_RULE = "ABL-NO-POLICY"


def unbound_grant(user_id: str, proposal_hash: str, now: datetime) -> ConfirmationGrant:
    """The stand-in the executor uses under NO_GRANTS: a grant that was never issued or checked."""
    return ConfirmationGrant(
        grant_id="grant_ablated", proposal_hash=proposal_hash, user_id=user_id,
        level=ConfirmationLevel.MFA, issued_at=now, expires_at=now + timedelta(seconds=120), used=True,
    )  # fmt: skip


def evaluate_ablated(
    abl: Ablations, proposal: ActionProposal, state: AccountState, cfg: PolicyConfig, now: datetime
) -> PolicyDecision:
    """``evaluate``, minus whichever of NO_TAINT and NO_POLICY is active (and nothing else)."""
    if abl.has(
        Ablation.NO_TAINT
    ):  # no taint tracking at all: the stored provenance is lost as well
        proposal = ActionProposal.model_validate(strip_taint(proposal.model_dump(mode="json")))
        state = state.model_copy(
            update={
                "payees": {
                    k: v.model_copy(update={"provenance": Provenance.USER_TYPED})
                    for k, v in state.payees.items()
                },
                "invoices": {
                    k: v.model_copy(update={"provenance": Provenance.SYSTEM})
                    for k, v in state.invoices.items()
                },
            }
        )
    if abl.has(Ablation.NO_POLICY):
        return PolicyDecision(
            decision=Decision.REQUIRE_CONFIRM, rule_ids=(FLOOR_RULE,),
            reasons=("ablation: rules removed, only the confirmation floor remains",),
            policy_version_hash=cfg.version_hash,
        )  # fmt: skip
    if abl.has(Ablation.NO_RISK):  # the same rules, with no risk score at all
        cfg = cfg.model_copy(update={"risk": cfg.risk.model_copy(update={"weights_bps": {}})})
        proposal = proposal.model_copy(update={"risk": None})
    if abl.has(Ablation.NO_COOLOFF):  # the same rules with a cap no payment can reach
        cfg = cfg.model_copy(
            update={
                "payee_cooloff": cfg.payee_cooloff.model_copy(
                    update={
                        "first_payment_cap_npr_minor": 10**15,
                        "untrusted_origin_cap_npr_minor": 10**15,
                    }
                )
            }
        )
    return evaluate(proposal, state, cfg, now)
