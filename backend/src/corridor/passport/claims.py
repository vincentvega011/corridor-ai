"""Claims for the Credit Passport, computed from the user's own payment history (no amounts).

Each claim is a small fact with counts only: no amounts, no account numbers, no payee names
(INV-12).
Claims are separate on purpose, so the holder can disclose one and keep the rest hidden. Only true
statements become claims: ``no_failed_payments`` exists only when no payment failed.
"""

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from corridor.sandbox.models import CreditEvent, Transfer

CATEGORY_CLAIMS = {
    "rent": "rent_on_time",
    "tuition": "tuition_on_time",
    "bill": "bills_on_time",
}
REMITTANCE_CLAIM = "money_sent_home"
STATEMENTS = {
    "rent_on_time": "rent payments made on time (count)",
    "tuition_on_time": "tuition payments made on time (count)",
    "bills_on_time": "bill payments made on time (count)",
    "money_sent_home": "money sent home (number of transfers and months)",
    "no_failed_payments": "failed payments (none, and for how many months)",
    "history_months": "months of payment history (count)",
}
"""What the confirmation card says each claim shows (fixed text, no values)."""
CLAIM_IDS = (*CATEGORY_CLAIMS.values(), REMITTANCE_CLAIM, "no_failed_payments", "history_months")


def build_claims(session: Session, user_id: str, today: date) -> list[dict[str, Any]]:
    """The claims this user can honestly make today, in a fixed order."""
    events = session.scalars(
        select(CreditEvent).where(CreditEvent.user_id == user_id).order_by(CreditEvent.due_date)
    ).all()
    if not events:
        return []
    claims: list[dict[str, Any]] = []
    for kind, claim_id in CATEGORY_CLAIMS.items():
        rows = [e for e in events if e.kind == kind]
        if rows:
            claims.append(
                {
                    "id": claim_id,
                    "statement": f"{kind} payments made on time",
                    "on_time_payments": sum(1 for e in rows if e.on_time),
                    "payments": len(rows),
                }
            )
    remit = [e for e in events if e.kind == "remittance"]
    if remit:
        claims.append(
            {
                "id": REMITTANCE_CLAIM,
                "statement": "money sent home",
                "payments": len(remit),
                "months": _months(remit[0].due_date, today),
            }
        )
    failed = session.scalars(
        select(Transfer).where(Transfer.user_id == user_id, Transfer.status == "failed")
    ).all()
    if not failed:
        claims.append(
            {
                "id": "no_failed_payments",
                "statement": "failed payments (none)",
                "failed_payments": 0,
                "months": _months(events[0].due_date, today),
            }
        )
    claims.append(
        {
            "id": "history_months",
            "statement": "months of payment history",
            "months": _months(events[0].due_date, today),
        }
    )
    return claims


def preview_claim_ids(session: Session, user_id: str, today: date) -> list[str]:
    """The claim ids a passport issued now would contain (shown on the confirmation card).

    The executor recomputes the claims at execution time; they are counts only."""
    return [str(c["id"]) for c in build_claims(session, user_id, today)]


def _months(first: date, today: date) -> int:
    return max((today.year - first.year) * 12 + today.month - first.month, 0)
