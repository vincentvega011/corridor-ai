"""provider daily send limit

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "providers",
        sa.Column("daily_limit_minor", sa.BigInteger, nullable=False, server_default="0"),
    )
    op.execute("UPDATE providers SET daily_limit_minor = 2 * max_minor")
    op.alter_column("providers", "daily_limit_minor", server_default=None)


def downgrade() -> None:
    op.drop_column("providers", "daily_limit_minor")
