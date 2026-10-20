"""security events can reference a document

A denial caused by a malicious document is recorded once per document hash (kind ``doc_denied``,
``ref`` = the hash) and counts at most once in total toward the safety lock (ADR 0006).

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("security_events", sa.Column("ref", sa.String(64), nullable=True))
    op.drop_constraint(op.f("ck_security_events_kind_valid"), "security_events")
    op.create_check_constraint(
        op.f("ck_security_events_kind_valid"),
        "security_events",
        "kind IN ('denied', 'failed_high_risk', 'totp_failed', 'critical', 'doc_denied')",
    )
    op.create_index("ix_security_events_ref", "security_events", ["user_id", "kind", "ref"])


def downgrade() -> None:
    op.drop_index("ix_security_events_ref", "security_events")
    op.execute("DELETE FROM security_events WHERE kind = 'doc_denied'")
    op.drop_constraint(op.f("ck_security_events_kind_valid"), "security_events")
    op.create_check_constraint(
        op.f("ck_security_events_kind_valid"),
        "security_events",
        "kind IN ('denied', 'failed_high_risk', 'totp_failed', 'critical')",
    )
    op.drop_column("security_events", "ref")
