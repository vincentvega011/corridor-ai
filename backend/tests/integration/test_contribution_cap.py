"""The daily cap on contribution requests per contributor, counted from the database (P3-1b).

Asking the same family member again and again is pressure, not a plan. The cap is a rolling 24 hours and
counts only this requester's requests to this one contributor."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from corridor.sandbox.account_state import build_account_state
from corridor.sandbox.models import ContributionRequest, FamilyLink
from executor_helpers import CFG

NOW = datetime(2026, 10, 12, 8, 0, tzinfo=UTC)


def add_requests(
    engine: Engine, contributor: str, ages_h: list[float], requester: str = "usr_priya"
) -> None:
    with Session(engine) as s, s.begin():
        acct = s.scalars(select(FamilyLink).where(FamilyLink.user_id == "usr_priya")).first()
        assert acct is not None
        for i, h in enumerate(ages_h):
            s.add(
                ContributionRequest(
                    id=f"cr_{contributor}_{requester}_{i}", requester_user_id=requester,
                    contributor_user_id=contributor, amount_minor=1_000, currency="QAR",
                    target_account_id="acc_priya_aud", invoice_id=None, status="pending",
                    idempotency_key=f"k_{contributor}_{requester}_{i}", proposal_hash="0" * 64,
                    created_at=NOW - timedelta(hours=h),
                )
            )  # fmt: skip


def contributor_of_priya(engine: Engine) -> str:
    with Session(engine) as s:
        link = s.scalars(select(FamilyLink).where(FamilyLink.user_id == "usr_priya")).first()
        assert link is not None
        return link.contributor_user_id


def count(engine: Engine, who: str) -> int:
    with Session(engine) as s:
        return build_account_state(s, "usr_priya", NOW, CFG).family_links[who].requests_last_24h


def test_the_count_is_a_rolling_24_hours_for_this_requester_and_this_contributor(
    sandbox: Engine,
) -> None:
    who = contributor_of_priya(sandbox)
    add_requests(sandbox, who, [1, 5, 23.5, 25, 48])  # three inside the window, two outside
    assert count(sandbox, who) == 3
    # someone else asking the same contributor does not use up Priya's cap
    add_requests(sandbox, who, [1, 2], requester="usr_anil")
    assert count(sandbox, who) == 3


def test_the_cap_comes_from_the_policy_file() -> None:
    assert CFG.contribution.max_requests_per_contributor_per_day == 3
