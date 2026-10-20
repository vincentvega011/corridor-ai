"""tuition funding: savings accounts, instalment invoices, family links and contribution requests

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts", sa.Column("kind", sa.String(16), nullable=False, server_default="spending")
    )
    op.create_check_constraint(
        op.f("ck_accounts_kind_valid"), "accounts", "kind IN ('spending', 'savings')"
    )
    op.drop_constraint("uq_accounts_user_currency", "accounts", type_="unique")
    op.create_unique_constraint(
        "uq_accounts_user_currency_kind", "accounts", ["user_id", "currency", "kind"]
    )
    op.add_column(
        "invoices", sa.Column("paid_minor", sa.BigInteger, nullable=False, server_default="0")
    )
    op.drop_constraint(op.f("ck_invoices_status_valid"), "invoices", type_="check")
    op.create_check_constraint(
        op.f("ck_invoices_status_valid"),
        "invoices",
        "status IN ('open', 'part_paid', 'paid', 'cancelled')",
    )
    op.create_check_constraint(
        op.f("ck_invoices_paid_in_range"),
        "invoices",
        "paid_minor >= 0 AND paid_minor <= amount_minor",
    )
    op.create_table(
        "family_links",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("contributor_user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "contributor_account_id", sa.String(64), sa.ForeignKey("accounts.id"), nullable=False
        ),
        sa.Column("relation", sa.String(32), nullable=False),
        sa.Column("per_request_limit_minor", sa.BigInteger, nullable=False),
        sa.CheckConstraint(
            "per_request_limit_minor > 0", name=op.f("ck_family_links_limit_positive")
        ),
        sa.CheckConstraint("user_id <> contributor_user_id", name=op.f("ck_family_links_not_self")),
        sa.UniqueConstraint("user_id", "contributor_user_id", name="uq_family_links_pair"),
    )
    op.create_table(
        "contribution_requests",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "requester_user_id",
            sa.String(64),
            sa.ForeignKey("users.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "contributor_user_id",
            sa.String(64),
            sa.ForeignKey("users.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("amount_minor", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("target_account_id", sa.String(64), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("invoice_id", sa.String(64), sa.ForeignKey("invoices.id"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("proposal_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "amount_minor > 0", name=op.f("ck_contribution_requests_amount_positive")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'declined')",
            name=op.f("ck_contribution_requests_status_valid"),
        ),
    )


def downgrade() -> None:
    op.drop_table("contribution_requests")
    op.drop_table("family_links")
    op.drop_constraint(op.f("ck_invoices_paid_in_range"), "invoices", type_="check")
    op.drop_constraint(op.f("ck_invoices_status_valid"), "invoices", type_="check")
    op.create_check_constraint(
        op.f("ck_invoices_status_valid"), "invoices", "status IN ('open', 'paid', 'cancelled')"
    )
    op.drop_column("invoices", "paid_minor")
    op.execute("DELETE FROM accounts WHERE kind = 'savings'")  # one account per currency again
    op.drop_constraint("uq_accounts_user_currency_kind", "accounts", type_="unique")
    op.create_unique_constraint("uq_accounts_user_currency", "accounts", ["user_id", "currency"])
    op.drop_constraint(op.f("ck_accounts_kind_valid"), "accounts", type_="check")
    op.drop_column("accounts", "kind")
