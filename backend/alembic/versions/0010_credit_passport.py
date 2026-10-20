"""credit passport: issued credentials and presentations

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "passports",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("token", sa.Text, nullable=False),
        sa.Column("disclosures", JSONB, nullable=False),
        sa.Column("claim_ids", JSONB, nullable=False),
        sa.Column("holder", sa.String(40), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
    )
    op.create_table(
        "passport_presentations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("passport_id", sa.String(64), sa.ForeignKey("passports.id"), nullable=False),
        sa.Column("audience", sa.String(80), nullable=False),
        sa.Column("claim_ids", JSONB, nullable=False),
        sa.Column("presentation", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
    )


def downgrade() -> None:
    op.drop_table("passport_presentations")
    op.drop_table("passports")
