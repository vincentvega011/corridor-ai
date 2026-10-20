"""documents table for the quarantined reader

Every document the reader processes is stored here with provenance ``untrusted`` and its injection
signals. ``build_account_state`` reads the flagged hashes so ``R-INJECTION`` fires at proposal time and
again at execution time (docs/spec/modules.md 5.5 and 5.7).

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("doc_hash", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("injection_suspected", sa.Boolean(), nullable=False),
        sa.Column(
            "signals",
            sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "fields",
            sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("provenance", sa.String(16), nullable=False, server_default="untrusted"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("doc_hash", "user_id", name=op.f("pk_documents")),
        sa.CheckConstraint(
            "kind IN ('invoice', 'message', 'statement')", name=op.f("ck_documents_kind_valid")
        ),
        sa.CheckConstraint("provenance = 'untrusted'", name=op.f("ck_documents_always_untrusted")),
        sa.CheckConstraint("doc_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_documents_hash_format")),
    )
    op.create_index("ix_documents_user_flagged", "documents", ["user_id", "injection_suspected"])


def downgrade() -> None:
    op.drop_table("documents")
