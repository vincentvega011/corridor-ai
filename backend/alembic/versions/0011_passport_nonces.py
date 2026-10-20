"""credit passport: nonces a verifier has already accepted (one use per presentation)

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "passport_nonces",
        sa.Column("nonce", sa.String(64), primary_key=True),
        sa.Column("audience", sa.String(80), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("passport_nonces")
