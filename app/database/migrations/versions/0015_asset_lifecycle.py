"""add point-in-time asset lifecycle and universe coverage

Revision ID: 0015_asset_lifecycle
Revises: 0014_corporate_actions
Create Date: 2026-08-22 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0015_asset_lifecycle"
down_revision = "0014_corporate_actions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "asset_lifecycle_records",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("listed_on", sa.Date(), nullable=True),
        sa.Column("delisted_on", sa.Date(), nullable=True),
        sa.Column("market", sa.String(length=128), nullable=True),
        sa.Column("sector_17", sa.String(length=128), nullable=True),
        sa.Column("sector_33", sa.String(length=128), nullable=True),
        sa.Column("investability_status", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.CheckConstraint("effective_to IS NULL OR effective_from <= effective_to", name="ck_asset_lifecycle_effective_period"),
        sa.CheckConstraint("delisted_on IS NULL OR listed_on IS NULL OR listed_on <= delisted_on", name="ck_asset_lifecycle_listing_period"),
        sa.CheckConstraint("investability_status IN ('investable', 'non_investable', 'suspended', 'delisted', 'unknown')", name="ck_asset_lifecycle_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_id", "effective_from", "source", "fetched_at", name="uq_asset_lifecycle_revision"),
    )
    op.create_index("ix_asset_lifecycle_asset_effective", "asset_lifecycle_records", ["asset_id", "effective_from"])
    op.create_table(
        "asset_universe_coverages",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("observed_asset_count", sa.Integer(), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint("period_start <= period_end", name="ck_asset_universe_coverage_period"),
        sa.CheckConstraint("status IN ('complete', 'partial', 'unavailable')", name="ck_asset_universe_coverage_status"),
        sa.CheckConstraint("observed_asset_count IS NULL OR observed_asset_count >= 0", name="ck_asset_universe_coverage_count"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("period_start", "period_end", "source", "checked_at", name="uq_asset_universe_coverage_revision"),
    )
    op.create_index("ix_asset_universe_coverage_period", "asset_universe_coverages", ["period_start", "period_end"])


def downgrade() -> None:
    op.drop_index("ix_asset_universe_coverage_period", table_name="asset_universe_coverages")
    op.drop_table("asset_universe_coverages")
    op.drop_index("ix_asset_lifecycle_asset_effective", table_name="asset_lifecycle_records")
    op.drop_table("asset_lifecycle_records")
