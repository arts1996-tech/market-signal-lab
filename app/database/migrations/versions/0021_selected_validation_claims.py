"""add selected-universe validation claims and classification

Revision ID: 0021_selection_claims
Revises: 0020_selected_backtests
Create Date: 2026-08-27 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0021_selection_claims"
down_revision = "0020_selected_backtests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "selected_universe_validation_claims",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("selection_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("selection_key", sa.String(64), nullable=False),
        sa.Column("selection_version", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("strategy_version", sa.String(64), nullable=False),
        sa.Column("input_data_version", sa.String(64), nullable=False),
        sa.Column("classification", sa.String(32), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("period_start <= period_end", name="ck_selected_universe_validation_period"),
        sa.CheckConstraint("classification IN ('precommitted_unseen', 'retrospective_user_selected')", name="ck_selected_universe_validation_classification"),
        sa.ForeignKeyConstraint(["selection_id"], ["user_asset_selections.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("selection_id", "period_start", "period_end", "strategy_version", "input_data_version", name="uq_selected_universe_validation_claim"),
    )
    op.create_index("ix_selected_universe_validation_lookup", "selected_universe_validation_claims", ["selection_key", "period_start", "period_end"])
    op.create_index(
        "uq_selected_universe_formal_period",
        "selected_universe_validation_claims",
        ["selection_key", "period_start", "period_end"],
        unique=True,
        postgresql_where=sa.text("classification = 'precommitted_unseen'"),
    )
    op.add_column("selected_universe_backtest_runs", sa.Column("validation_claim_id", postgresql.UUID(as_uuid=False)))
    op.add_column("selected_universe_backtest_runs", sa.Column("evaluation_classification", sa.String(32), nullable=False, server_default="retrospective_user_selected"))
    op.create_foreign_key(
        "fk_selected_universe_backtest_validation_claim",
        "selected_universe_backtest_runs",
        "selected_universe_validation_claims",
        ["validation_claim_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_selected_universe_backtest_validation_claim", "selected_universe_backtest_runs", type_="foreignkey")
    op.drop_column("selected_universe_backtest_runs", "evaluation_classification")
    op.drop_column("selected_universe_backtest_runs", "validation_claim_id")
    op.drop_index("uq_selected_universe_formal_period", table_name="selected_universe_validation_claims")
    op.drop_index("ix_selected_universe_validation_lookup", table_name="selected_universe_validation_claims")
    op.drop_table("selected_universe_validation_claims")
