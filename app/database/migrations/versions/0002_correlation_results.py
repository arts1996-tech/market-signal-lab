"""add correlation results

Revision ID: 0002_correlation_results
Revises: 0001_initial
Create Date: 2026-07-11 00:10:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_correlation_results"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "correlation_results",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("analysis_name", sa.String(length=128), nullable=False),
        sa.Column("base_symbol", sa.String(length=32), nullable=False),
        sa.Column("target_symbol", sa.String(length=32), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(length=64), nullable=False),
        sa.Column("lag_rule", sa.String(length=128), nullable=False),
        sa.Column("correlation", sa.Numeric(precision=12, scale=8), nullable=True),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "analysis_name",
            "base_symbol",
            "target_symbol",
            "window_days",
            "period_end",
            "method",
            name="uq_correlation_result",
        ),
    )
    op.create_index(
        "ix_correlation_results_lookup",
        "correlation_results",
        ["analysis_name", "base_symbol", "target_symbol", "period_end"],
    )


def downgrade() -> None:
    op.drop_index("ix_correlation_results_lookup", table_name="correlation_results")
    op.drop_table("correlation_results")
