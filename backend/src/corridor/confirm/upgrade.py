"""The only place a value becomes ``USER_CONFIRMED`` (docs/spec/modules.md 5.6 and 5.8, INV-4).

A value is upgraded only after the user confirmed the exact proposal that contains it: the grant
must be consumed, bound to this proposal's hash and user, and not expired. Values from the bank's
own records (``SYSTEM``) are left alone. A repo test fails if ``USER_CONFIRMED`` is created
anywhere else.
"""

from datetime import datetime
from typing import Any

from corridor.contracts.action import ActionProposal
from corridor.contracts.confirm import ConfirmationGrant
from corridor.contracts.provenance import Provenance, Tainted


class UpgradeError(Exception):
    """The grant does not cover this proposal."""


def _confirmed(value: Tainted[Any]) -> Tainted[Any]:
    if value.provenance is Provenance.SYSTEM:
        return value
    return Tainted[Any](
        value=value.value, provenance=Provenance.USER_CONFIRMED, source_ref=value.source_ref
    )


def confirm_values(
    proposal: ActionProposal, grant: ConfirmationGrant, now: datetime
) -> ActionProposal:
    """Return ``proposal`` with its source account, payee and amount marked user confirmed.

    Raises ``UpgradeError`` unless ``grant`` was consumed for exactly this proposal and user."""
    if (
        not grant.used
        or grant.user_id != proposal.user_id
        or grant.proposal_hash != proposal.canonical_hash()
        or grant.is_expired(now)
    ):
        raise UpgradeError("grant does not cover this proposal")
    return proposal.model_copy(
        update={
            "source_account_id": _confirmed(proposal.source_account_id),
            "payee_id": None if proposal.payee_id is None else _confirmed(proposal.payee_id),
            "amount": None if proposal.amount is None else _confirmed(proposal.amount),
        }
    )
