"""add corporate actions and verified coverage

Revision ID: 0014_corporate_actions
Revises: 0013_asset_analysis_results
Create Date: 2026-08-22 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0014_corporate_actions"
down_revision = "0013_asset_analysis_results"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "corporate_actions",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_event_id", sa.String(length=128), nullable=False),
        sa.Column("announced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("ex_date", sa.Date(), nullable=True),
        sa.Column("record_date", sa.Date(), nullable=True),
        sa.Column("payable_date", sa.Date(), nullable=True),
        sa.Column("ratio", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("cash_per_share", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.CheckConstraint(
            "action_type IN ('stock_split', 'reverse_split', 'cash_dividend', 'merger', 'share_exchange')",
            name="ck_corporate_action_type",
        ),
        sa.CheckConstraint(
            "status IN ('confirmed', 'pending', 'cancelled')",
            name="ck_corporate_action_status",
        ),
        sa.CheckConstraint(
            "(action_type != 'stock_split' OR (ratio IS NOT NULL AND ratio > 1)) AND "
            "(action_type != 'reverse_split' OR (ratio IS NOT NULL AND ratio > 0 AND ratio < 1))",
            name="ck_corporate_action_ratio",
        ),
        sa.CheckConstraint(
            "action_type != 'cash_dividend' OR "
            "(ex_date IS NOT NULL AND record_date IS NOT NULL AND payable_date IS NOT NULL "
            "AND ex_date <= record_date AND record_date <= payable_date "
            "AND cash_per_share IS NOT NULL AND cash_per_share >= 0 AND currency IS NOT NULL)",
            name="ck_corporate_action_dividend_terms",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source",
            "source_event_id",
            "fetched_at",
            name="uq_corporate_action_source_event",
        ),
    )
    op.create_index(
        "ix_corporate_actions_asset_effective",
        "corporate_actions",
        ["asset_id", "effective_date"],
    )
    op.create_table(
        "corporate_action_coverages",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.CheckConstraint(
            "period_start <= period_end",
            name="ck_corporate_action_coverage_period",
        ),
        sa.CheckConstraint(
            "status IN ('complete', 'partial', 'unavailable')",
            name="ck_corporate_action_coverage_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "asset_id",
            "period_start",
            "period_end",
            "source",
            "checked_at",
            name="uq_corporate_action_coverage_period",
        ),
    )
    op.create_index(
        "ix_corporate_action_coverages_asset_period",
        "corporate_action_coverages",
        ["asset_id", "period_start", "period_end"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_corporate_action_coverages_asset_period",
        table_name="corporate_action_coverages",
    )
    op.drop_table("corporate_action_coverages")
    op.drop_index(
        "ix_corporate_actions_asset_effective", table_name="corporate_actions"
    )
    op.drop_table("corporate_actions")
