"""add versioned all-asset analysis runs and results

Revision ID: 0013_asset_analysis_results
Revises: 0012_decision_tracks
Create Date: 2026-08-16 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0013_asset_analysis_results"
down_revision = "0012_decision_tracks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "asset_analysis_runs",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("analysis_name", sa.String(length=64), nullable=False),
        sa.Column("data_scope", sa.String(length=32), nullable=False),
        sa.Column("rule_version", sa.String(length=64), nullable=False),
        sa.Column("source_policy_version", sa.String(length=64), nullable=False),
        sa.Column("input_data_version", sa.String(length=64), nullable=False),
        sa.Column("data_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("assets_considered", sa.Integer(), nullable=False),
        sa.Column("eligible_asset_count", sa.Integer(), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "analysis_name",
            "data_scope",
            "rule_version",
            "input_data_version",
            name="uq_asset_analysis_run_input",
        ),
    )
    op.create_index(
        "ix_asset_analysis_runs_latest",
        "asset_analysis_runs",
        ["analysis_name", "data_scope", "status", "completed_at"],
    )
    op.create_table(
        "asset_analysis_results",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("asset_type", sa.String(length=32), nullable=False),
        sa.Column("sector", sa.String(length=128), nullable=False),
        sa.Column("data_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observations", sa.Integer(), nullable=False),
        sa.Column("attention_score", sa.Integer(), nullable=False),
        sa.Column("movement_score", sa.Integer(), nullable=True),
        sa.Column("attention_rank", sa.Integer(), nullable=False),
        sa.Column("movement_rank", sa.Integer(), nullable=True),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["asset_analysis_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "asset_id", name="uq_asset_analysis_result_asset"
        ),
    )
    op.create_index(
        "ix_asset_analysis_results_attention",
        "asset_analysis_results",
        ["run_id", "attention_rank"],
    )
    op.create_index(
        "ix_asset_analysis_results_movement",
        "asset_analysis_results",
        ["run_id", "movement_rank"],
    )
    op.create_index(
        "ix_asset_analysis_results_filter",
        "asset_analysis_results",
        ["run_id", "asset_type", "sector", "attention_score"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_asset_analysis_results_filter", table_name="asset_analysis_results"
    )
    op.drop_index(
        "ix_asset_analysis_results_movement", table_name="asset_analysis_results"
    )
    op.drop_index(
        "ix_asset_analysis_results_attention", table_name="asset_analysis_results"
    )
    op.drop_table("asset_analysis_results")
    op.drop_index("ix_asset_analysis_runs_latest", table_name="asset_analysis_runs")
    op.drop_table("asset_analysis_runs")
