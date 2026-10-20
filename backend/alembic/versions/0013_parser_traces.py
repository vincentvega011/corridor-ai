"""trace store for the parser's masked output, referenced from the ledger by hash (P3-4b)

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "parser_traces",
        sa.Column("ref_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("masked_output", sa.Text, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("parser_traces")
