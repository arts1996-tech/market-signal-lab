"""add append-only trade mode backtest results

Revision ID: 0024_trade_mode_backtests
Revises: 0023_margin_snapshots
Create Date: 2026-08-28 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0024_trade_mode_backtests"
down_revision = "0023_margin_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trade_mode_backtest_runs",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("horizon", sa.String(32), nullable=False),
        sa.Column("trade_mode", sa.String(32), nullable=False),
        sa.Column("account_name", sa.String(64), nullable=False),
        sa.Column("initial_cash", sa.Numeric(20, 4), nullable=False),
        sa.Column("strategy_version", sa.String(64), nullable=False),
        sa.Column("execution_version", sa.String(64), nullable=False),
        sa.Column("data_scope", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("research_only", sa.Boolean(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "trade_mode IN ('cash', 'margin_long', 'margin_short', 'auto_select')",
            name="ck_trade_mode_backtest_mode",
        ),
        sa.CheckConstraint(
            "status IN ('success', 'insufficient_data')",
            name="ck_trade_mode_backtest_status",
        ),
        sa.CheckConstraint(
            "data_scope IN ('synthetic_research', 'delayed_historical')",
            name="ck_trade_mode_backtest_data_scope",
        ),
        sa.CheckConstraint(
            "initial_cash > 0",
            name="ck_trade_mode_backtest_initial_cash",
        ),
        sa.CheckConstraint(
            "research_only = true",
            name="ck_trade_mode_backtest_research_only",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_trade_mode_backtest_run_id"),
    )
    op.create_index(
        "ix_trade_mode_backtest_lookup",
        "trade_mode_backtest_runs",
        ["horizon", "trade_mode", "created_at"],
    )
    op.execute(
        """
        CREATE FUNCTION reject_trade_mode_backtest_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'trade mode backtest history is append-only: % is not allowed', TG_OP;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_trade_mode_backtest_runs_append_only
        BEFORE UPDATE OR DELETE ON trade_mode_backtest_runs
        FOR EACH ROW EXECUTE FUNCTION reject_trade_mode_backtest_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_trade_mode_backtest_runs_append_only
        ON trade_mode_backtest_runs
        """
    )
    op.execute("DROP FUNCTION IF EXISTS reject_trade_mode_backtest_mutation()")
    op.drop_index(
        "ix_trade_mode_backtest_lookup",
        table_name="trade_mode_backtest_runs",
    )
    op.drop_table("trade_mode_backtest_runs")
