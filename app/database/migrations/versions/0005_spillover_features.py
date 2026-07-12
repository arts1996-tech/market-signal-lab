"""add observed US-Japan spillover features

Revision ID: 0005_spillover_features
Revises: 0004_price_collection_progress
Create Date: 2026-07-12 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0005_spillover_features"
down_revision = "0004_price_collection_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "spillover_features",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("base_symbol", sa.String(length=32), nullable=False),
        sa.Column("target_symbol", sa.String(length=32), nullable=False),
        sa.Column("japan_session_date", sa.Date(), nullable=False),
        sa.Column("us_session_date", sa.Date(), nullable=False),
        sa.Column("metric", sa.String(length=32), nullable=False),
        sa.Column("us_return", sa.Numeric(precision=12, scale=8), nullable=False),
        sa.Column("target_return", sa.Numeric(precision=12, scale=8), nullable=False),
        sa.Column("lag_rule", sa.String(length=128), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "base_symbol", "target_symbol", "japan_session_date", "metric", name="uq_spillover_feature"
        ),
    )
    op.create_index(
        "ix_spillover_features_lookup",
        "spillover_features",
        ["base_symbol", "target_symbol", "japan_session_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_spillover_features_lookup", table_name="spillover_features")
    op.drop_table("spillover_features")
