"""Add one pending contribution request (Priya asks her uncle Ramesh) for the browser tests. Synthetic only."""

import os
import sys
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from corridor.sandbox.db import get_engine
from corridor.sandbox.models import ContributionRequest


def main() -> int:
    from sqlalchemy.engine import make_url

    if not (make_url(os.environ.get("DATABASE_URL", "x:///")).database or "").endswith("_e2e"):
        print("refusing: the database name must end with _e2e", file=sys.stderr)
        return 2
    with Session(get_engine()) as s, s.begin():
        s.add(
            ContributionRequest(
                id="cr_e2e_1", requester_user_id="usr_priya", contributor_user_id="usr_ramesh",
                amount_minor=333_255, currency="QAR", target_account_id="acc_priya_aud",
                invoice_id="inv_priya_tuition_2026s2", status="pending",
                idempotency_key="idem-e2e-cr-1", proposal_hash="0" * 64, created_at=datetime.now(UTC),
            )
        )  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
