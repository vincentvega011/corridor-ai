"""bank verification requests; passport verifier challenges replace the holder nonces

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("passport_nonces")
    op.create_table(
        "passport_challenges",
        sa.Column("nonce", sa.String(64), primary_key=True),
        sa.Column("audience", sa.String(80), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_by", sa.String(16), nullable=False, server_default="verifier"),
    )
    op.create_table(
        "verification_requests",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column(
            "payee_id", sa.String(64), sa.ForeignKey("payees.id"), nullable=False, index=True
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("officer_id", sa.String(64), nullable=True),
        sa.Column("refusal", sa.String(64), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'verified', 'refused')",
            name=op.f("ck_verification_requests_status_valid"),
        ),
    )


def downgrade() -> None:
    op.drop_table("verification_requests")
    op.drop_table("passport_challenges")
    op.create_table(
        "passport_nonces",
        sa.Column("nonce", sa.String(64), primary_key=True),
        sa.Column("audience", sa.String(80), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
    )
