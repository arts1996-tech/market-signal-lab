"""add persisted spillover regression results

Revision ID: 0006_spillover_model_results
Revises: 0005_spillover_features
Create Date: 2026-07-12 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0006_spillover_model_results"
down_revision = "0005_spillover_features"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "spillover_model_results",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("analysis_name", sa.String(length=128), nullable=False),
        sa.Column("base_symbol", sa.String(length=32), nullable=False),
        sa.Column("target_symbol", sa.String(length=32), nullable=False),
        sa.Column("target_metric", sa.String(length=32), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(length=64), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("r_squared", sa.Numeric(precision=12, scale=8), nullable=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model_version", sa.String(length=32), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "analysis_name",
            "base_symbol",
            "target_symbol",
            "target_metric",
            "window_days",
            "period_end",
            "method",
            name="uq_spillover_model_result",
        ),
    )
    op.create_index(
        "ix_spillover_model_results_lookup",
        "spillover_model_results",
        ["base_symbol", "target_symbol", "target_metric", "period_end"],
    )


def downgrade() -> None:
    op.drop_index("ix_spillover_model_results_lookup", table_name="spillover_model_results")
    op.drop_table("spillover_model_results")
