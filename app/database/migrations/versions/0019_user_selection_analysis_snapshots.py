"""add immutable selected-universe analysis snapshots

Revision ID: 0019_selection_analysis
Revises: 0018_user_asset_selections
Create Date: 2026-08-26 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0019_selection_analysis"
down_revision = "0018_user_asset_selections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_asset_selection_analysis_runs",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("selection_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("selection_key", sa.String(64), nullable=False),
        sa.Column("selection_version", sa.Integer(), nullable=False),
        sa.Column("selection_composition_hash", sa.String(64), nullable=False),
        sa.Column("source_asset_analysis_run_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("data_scope", sa.String(32), nullable=False),
        sa.Column("analysis_rule_version", sa.String(64), nullable=False),
        sa.Column("source_policy_version", sa.String(64), nullable=False),
        sa.Column("input_data_version", sa.String(64), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("data_as_of", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('success', 'partial', 'insufficient_data')",
            name="ck_user_selection_analysis_run_status",
        ),
        sa.ForeignKeyConstraint(["selection_id"], ["user_asset_selections.id"]),
        sa.ForeignKeyConstraint(["source_asset_analysis_run_id"], ["asset_analysis_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "selection_id", "source_asset_analysis_run_id", name="uq_user_selection_analysis_source"
        ),
    )
    op.create_index(
        "ix_user_selection_analysis_runs_latest",
        "user_asset_selection_analysis_runs",
        ["selection_id", "created_at"],
    )
    op.create_table(
        "user_asset_selection_analysis_results",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("source_asset_analysis_result_id", postgresql.UUID(as_uuid=False)),
        sa.Column("analysis_status", sa.String(32), nullable=False),
        sa.Column("data_as_of", sa.DateTime(timezone=True)),
        sa.Column("observations", sa.Integer()),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("quality_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "analysis_status IN ('analyzed', 'insufficient_data')",
            name="ck_user_selection_analysis_result_status",
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["user_asset_selection_analysis_runs.id"]),
        sa.ForeignKeyConstraint(["source_asset_analysis_result_id"], ["asset_analysis_results.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "asset_id", name="uq_user_selection_analysis_result_asset"),
    )
    op.create_index(
        "ix_user_selection_analysis_results_status",
        "user_asset_selection_analysis_results",
        ["run_id", "analysis_status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_selection_analysis_results_status",
        table_name="user_asset_selection_analysis_results",
    )
    op.drop_table("user_asset_selection_analysis_results")
    op.drop_index(
        "ix_user_selection_analysis_runs_latest",
        table_name="user_asset_selection_analysis_runs",
    )
    op.drop_table("user_asset_selection_analysis_runs")
