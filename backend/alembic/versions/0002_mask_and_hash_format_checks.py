"""mask and hash format checks

Defence in depth for INV-12: the database itself refuses anything but ``****1234`` in
``account_no_masked`` and a 64 character lowercase hex digest in ``account_no_hash``, so a bug
can never persist a full account number.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

MASKED = "account_no_masked ~ '^\\*\\*\\*\\*[0-9]{4}$'"
HASH = "account_no_hash ~ '^[0-9a-f]{64}$'"


def upgrade() -> None:
    for table in ("accounts", "payees"):
        op.create_check_constraint("masked_format", table, MASKED)
        op.create_check_constraint("hash_format", table, HASH)


def downgrade() -> None:
    for table in ("accounts", "payees"):
        op.drop_constraint(op.f(f"ck_{table}_hash_format"), table, type_="check")
        op.drop_constraint(op.f(f"ck_{table}_masked_format"), table, type_="check")
