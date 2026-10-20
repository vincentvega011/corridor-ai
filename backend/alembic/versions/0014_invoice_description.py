"""invoice description in words, shown to customers instead of the id (P4-1)

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("description", sa.String(80), nullable=True))


def downgrade() -> None:
    op.drop_column("invoices", "description")
