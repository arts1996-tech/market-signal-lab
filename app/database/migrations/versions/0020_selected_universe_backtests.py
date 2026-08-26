"""add selected-universe cash backtest audit records

Revision ID: 0020_selected_backtests
Revises: 0019_selection_analysis
Create Date: 2026-08-26 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0020_selected_backtests"
down_revision = "0019_selection_analysis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "selected_universe_backtest_runs",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("analysis_snapshot_run_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("selection_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("selection_key", sa.String(64), nullable=False),
        sa.Column("selection_version", sa.Integer(), nullable=False),
        sa.Column("selection_composition_hash", sa.String(64), nullable=False),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("horizon", sa.String(32), nullable=False),
        sa.Column("trade_mode", sa.String(32), nullable=False),
        sa.Column("initial_cash", sa.Numeric(20, 4), nullable=False),
        sa.Column("strategy_version", sa.String(64), nullable=False),
        sa.Column("execution_version", sa.String(64), nullable=False),
        sa.Column("data_scope", sa.String(32), nullable=False),
        sa.Column("input_data_version", sa.String(64), nullable=False),
        sa.Column("simulation_hash", sa.String(64), nullable=False),
        sa.Column("period_start", sa.Date()),
        sa.Column("period_end", sa.Date()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("horizon IN ('short_term', 'mid_term')", name="ck_selected_universe_backtest_horizon"),
        sa.CheckConstraint("trade_mode = 'cash'", name="ck_selected_universe_backtest_cash_only"),
        sa.CheckConstraint("status IN ('success', 'insufficient_data')", name="ck_selected_universe_backtest_status"),
        sa.ForeignKeyConstraint(["analysis_snapshot_run_id"], ["user_asset_selection_analysis_runs.id"]),
        sa.ForeignKeyConstraint(["selection_id"], ["user_asset_selections.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("analysis_snapshot_run_id", "horizon", "simulation_hash", name="uq_selected_universe_backtest_input"),
    )
    op.create_index("ix_selected_universe_backtest_latest", "selected_universe_backtest_runs", ["selection_id", "created_at"])
    op.create_table(
        "selected_universe_backtest_asset_results",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reason_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("signal_count", sa.Integer(), nullable=False),
        sa.Column("transaction_count", sa.Integer(), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(20, 4)),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('eligible', 'insufficient_data')", name="ck_selected_universe_backtest_asset_status"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["selected_universe_backtest_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "asset_id", name="uq_selected_universe_backtest_asset"),
    )
    op.create_index("ix_selected_universe_backtest_asset_status", "selected_universe_backtest_asset_results", ["run_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_selected_universe_backtest_asset_status", table_name="selected_universe_backtest_asset_results")
    op.drop_table("selected_universe_backtest_asset_results")
    op.drop_index("ix_selected_universe_backtest_latest", table_name="selected_universe_backtest_runs")
    op.drop_table("selected_universe_backtest_runs")
