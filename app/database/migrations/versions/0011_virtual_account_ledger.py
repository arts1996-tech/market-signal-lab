"""add append-only virtual account ledger

Revision ID: 0011_virtual_account_ledger
Revises: 0010_asset_sec_cik
Create Date: 2026-08-16 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0011_virtual_account_ledger"
down_revision = "0010_asset_sec_cik"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "virtual_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("account_name", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("initial_cash", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("strategy_version", sa.String(length=64), nullable=False),
        sa.Column("state_version", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_name", name="uq_virtual_accounts_account_name"),
    )
    op.create_table(
        "virtual_account_daily_states",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_market_session", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_data_version", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("cash", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("equity", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("unrealized_pnl", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("cumulative_pnl", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("maximum_drawdown", sa.Numeric(precision=12, scale=8), nullable=False),
        sa.Column("risk_halted", sa.Boolean(), nullable=False),
        sa.Column("positions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("pending_orders", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("signal_history", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["virtual_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id", "session_date", name="uq_virtual_account_daily_state_session"
        ),
    )
    op.create_index(
        "ix_virtual_account_daily_states_lookup",
        "virtual_account_daily_states",
        ["account_id", "session_date"],
        unique=False,
    )
    op.create_table(
        "virtual_account_events",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("daily_state_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_data_version", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["virtual_accounts.id"]),
        sa.ForeignKeyConstraint(
            ["daily_state_id"], ["virtual_account_daily_states.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id", "event_id", name="uq_virtual_account_event_id"
        ),
    )
    op.create_index(
        "ix_virtual_account_events_lookup",
        "virtual_account_events",
        ["account_id", "session_date", "event_type"],
        unique=False,
    )

    op.execute(
        """
        CREATE FUNCTION reject_virtual_account_ledger_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'virtual account ledger is append-only: % is not allowed', TG_OP;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table_name in (
        "virtual_accounts",
        "virtual_account_daily_states",
        "virtual_account_events",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_append_only
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION reject_virtual_account_ledger_mutation()
            """
        )


def downgrade() -> None:
    for table_name in (
        "virtual_account_events",
        "virtual_account_daily_states",
        "virtual_accounts",
    ):
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table_name}_append_only ON {table_name}"
        )
    op.execute("DROP FUNCTION IF EXISTS reject_virtual_account_ledger_mutation()")
    op.drop_index(
        "ix_virtual_account_events_lookup", table_name="virtual_account_events"
    )
    op.drop_table("virtual_account_events")
    op.drop_index(
        "ix_virtual_account_daily_states_lookup",
        table_name="virtual_account_daily_states",
    )
    op.drop_table("virtual_account_daily_states")
    op.drop_table("virtual_accounts")
