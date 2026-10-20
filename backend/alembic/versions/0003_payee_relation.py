"""payee relation

Adds ``payees.relation`` (mother, father, landlord, university ...) so the agent can resolve "Ama" or
"mother" to a stored payee deterministically, without any model involvement (INV-4).

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payees", sa.Column("relation", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("payees", "relation")
