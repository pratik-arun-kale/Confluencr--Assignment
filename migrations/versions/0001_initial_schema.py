"""Initial schema: accounts, transfers, transactions + immutability triggers.

Revision ID: 0001
Revises:
Create Date: 2026-10-27
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

NOW = sa.text("clock_timestamp()")
MONEY = sa.Numeric(18, 2)


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("balance", MONEY, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_accounts_name_not_blank"),
        sa.CheckConstraint("balance >= 0", name="ck_accounts_balance_non_negative"),
    )

    op.create_table(
        "transfers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("from_account_id", UUID(as_uuid=True), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("to_account_id", UUID(as_uuid=True), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("reference_id", sa.String(100), nullable=False),
        sa.Column("description", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("amount > 0", name="ck_transfers_amount_positive"),
        sa.CheckConstraint("from_account_id <> to_account_id", name="ck_transfers_distinct_accounts"),
        sa.UniqueConstraint("from_account_id", "reference_id", name="uq_transfers_from_reference"),
    )

    op.create_table(
        "transactions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("seq", sa.BigInteger, sa.Identity(always=True), nullable=False, unique=True),
        sa.Column("account_id", UUID(as_uuid=True), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("type", sa.String(6), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("description", sa.String(255)),
        sa.Column("reference_id", sa.String(100), nullable=False),
        sa.Column("transfer_id", UUID(as_uuid=True), sa.ForeignKey("transfers.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("type IN ('credit', 'debit')", name="ck_transactions_type"),
        sa.CheckConstraint("amount > 0", name="ck_transactions_amount_positive"),
        sa.CheckConstraint(
            "length(btrim(reference_id)) > 0", name="ck_transactions_reference_not_blank"
        ),
        sa.UniqueConstraint("account_id", "reference_id", name="uq_transactions_account_reference"),
    )
    op.create_index(
        "ix_transactions_account_created",
        "transactions",
        ["account_id", sa.text("created_at DESC"), sa.text("seq DESC")],
    )
    op.create_index(
        "ix_transactions_account_type_created",
        "transactions",
        ["account_id", "type", sa.text("created_at DESC"), sa.text("seq DESC")],
    )

    # Ledger records are append-only: block UPDATE/DELETE at the database level,
    # so immutability holds even for code paths (or people) that bypass the API.
    op.execute(
        """
        CREATE FUNCTION prevent_ledger_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% on % is not allowed: ledger records are immutable',
                TG_OP, TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END;
        $$;
        """
    )
    for table in ("transactions", "transfers"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_immutable
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION prevent_ledger_mutation();
            """
        )


def downgrade() -> None:
    for table in ("transactions", "transfers"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table}")
    op.execute("DROP FUNCTION IF EXISTS prevent_ledger_mutation()")
    op.drop_table("transactions")
    op.drop_table("transfers")
    op.drop_table("accounts")
