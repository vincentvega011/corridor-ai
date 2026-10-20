"""safety core: provenance columns, grants, safety lock, hash-chained ledger

Adds provenance columns for text that can originate from a document (payees, invoices), the
confirmation grant store, MFA and lock state, and the append-only ledger. The ledger tables are
writable only through the NOLOGIN role ``corridor_ledger`` (INSERT and SELECT), and row triggers
refuse UPDATE and DELETE for every role, so tampering needs an explicit admin action (ADR 0007).

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

PROV = "'system', 'user_typed', 'user_confirmed', 'untrusted', 'model_inferred'"


def upgrade() -> None:
    for table in ("payees", "invoices"):
        op.add_column(
            table,
            sa.Column("provenance", sa.String(length=16), nullable=False, server_default="system"),
        )
        op.create_check_constraint(
            op.f(f"ck_{table}_provenance_valid"), table, f"provenance IN ({PROV})"
        )

    op.create_table(
        "confirmation_grants",
        sa.Column("grant_id", sa.String(128), primary_key=True),
        sa.Column("proposal_hash", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("level", sa.String(8), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "level IN ('confirm', 'mfa')", name=op.f("ck_confirmation_grants_level_valid")
        ),
        sa.CheckConstraint(
            "expires_at = issued_at + interval '120 seconds'",
            name=op.f("ck_confirmation_grants_ttl"),
        ),
    )
    op.create_index(
        "ix_confirmation_grants_user", "confirmation_grants", ["user_id", "proposal_hash"]
    )
    op.create_table(
        "security_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.CheckConstraint(
            "kind IN ('denied', 'failed_high_risk', 'totp_failed', 'critical')",
            name=op.f("ck_security_events_kind_valid"),
        ),
    )
    op.create_index("ix_security_events_user_ts", "security_events", ["user_id", "ts"])
    op.create_table(
        "safety_locks",
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("locked", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("unlocked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "totp_steps",
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("step", sa.BigInteger(), primary_key=True),
    )

    op.create_table(
        "ledger_entries",
        sa.Column("seq", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("actor", sa.String(16), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("payload_canonical_json", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("policy_version_hash", sa.String(64), nullable=False),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("entry_hash", sa.String(64), nullable=False, unique=True),
        sa.CheckConstraint("seq >= 1", name=op.f("ck_ledger_entries_seq_positive")),
        sa.CheckConstraint(
            "payload_hash ~ '^[0-9a-f]{64}$' AND policy_version_hash ~ '^[0-9a-f]{64}$' "
            "AND prev_hash ~ '^[0-9a-f]{64}$' AND entry_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_ledger_entries_hash_format"),
        ),
    )
    op.create_table(
        "ledger_roots",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("scope", sa.String(8), nullable=False),
        sa.Column("scope_key", sa.String(128), nullable=False),
        sa.Column("first_seq", sa.BigInteger(), nullable=False),
        sa.Column("last_seq", sa.BigInteger(), nullable=False),
        sa.Column("entry_count", sa.Integer(), nullable=False),
        sa.Column("root_hash", sa.String(64), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("scope IN ('day', 'session')", name=op.f("ck_ledger_roots_scope_valid")),
    )
    op.create_index("ix_ledger_roots_scope", "ledger_roots", ["scope", "scope_key"])

    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'corridor_ledger') THEN
            CREATE ROLE corridor_ledger NOLOGIN;
          END IF;
        END $$
        """
    )
    op.execute("GRANT corridor_ledger TO CURRENT_USER")
    op.execute("REVOKE ALL ON ledger_entries, ledger_roots FROM PUBLIC")
    op.execute("GRANT SELECT, INSERT ON ledger_entries, ledger_roots TO corridor_ledger")
    op.execute("GRANT USAGE ON SEQUENCE ledger_roots_id_seq TO corridor_ledger")
    op.execute(
        """
        CREATE FUNCTION ledger_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'the audit ledger is append-only'; END $$
        """
    )
    for table in ("ledger_entries", "ledger_roots"):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION ledger_append_only()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION ledger_append_only()"
        )


def downgrade() -> None:
    for table in ("ledger_entries", "ledger_roots"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_truncate ON {table}")
    op.execute("DROP FUNCTION IF EXISTS ledger_append_only()")
    op.drop_table("ledger_roots")
    op.drop_table("ledger_entries")
    op.drop_table("totp_steps")
    op.drop_table("safety_locks")
    op.drop_table("security_events")
    op.drop_table("confirmation_grants")
    for table in ("invoices", "payees"):
        op.drop_constraint(op.f(f"ck_{table}_provenance_valid"), table)
        op.drop_column(table, "provenance")
