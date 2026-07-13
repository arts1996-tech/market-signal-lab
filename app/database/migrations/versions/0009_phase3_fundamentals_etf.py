"""add phase 3 fundamental and ETF metric snapshots

Revision ID: 0009_phase3_fundamentals_etf
Revises: 0008_market_price_basis
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009_phase3_fundamentals_etf"
down_revision = "0008_market_price_basis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fundamental_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("asset_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("assets.id"), nullable=False),
        sa.Column("disclosed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("sales", sa.Numeric(24, 6)), sa.Column("operating_profit", sa.Numeric(24, 6)),
        sa.Column("net_income", sa.Numeric(24, 6)), sa.Column("eps", sa.Numeric(18, 8)),
        sa.Column("equity", sa.Numeric(24, 6)), sa.Column("total_assets", sa.Numeric(24, 6)),
        sa.Column("operating_cashflow", sa.Numeric(24, 6)),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.UniqueConstraint("asset_id", "disclosed_at", "period_end", "source", name="uq_fundamental_snapshot"),
    )
    op.create_index("ix_fundamental_snapshots_asset_period", "fundamental_snapshots", ["asset_id", "period_end"])
    op.create_table(
        "etf_metric_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("asset_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("assets.id"), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("details", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("asset_id", "observed_at", "source", name="uq_etf_metric_snapshot"),
    )
    op.create_index("ix_etf_metric_snapshots_asset_observed", "etf_metric_snapshots", ["asset_id", "observed_at"])


def downgrade() -> None:
    op.drop_index("ix_etf_metric_snapshots_asset_observed", table_name="etf_metric_snapshots")
    op.drop_table("etf_metric_snapshots")
    op.drop_index("ix_fundamental_snapshots_asset_period", table_name="fundamental_snapshots")
    op.drop_table("fundamental_snapshots")
