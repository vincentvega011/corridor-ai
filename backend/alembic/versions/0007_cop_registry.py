"""simulated Confirmation of Payee registry

A registered holder name per account (synthetic bank data). A bank officer can verify a payee only when the
payee's name matches the holder name registered for its account (ADR 0012).

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cop_registry",
        sa.Column("account_no_hash", sa.String(64), primary_key=True),
        sa.Column("holder_name", sa.String(80), nullable=False),
        sa.Column("synthetic", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.CheckConstraint("account_no_hash ~ '^[0-9a-f]{64}$'", name="hash_format"),
        sa.CheckConstraint("synthetic", name="always_synthetic"),
    )


def downgrade() -> None:
    op.drop_table("cop_registry")
