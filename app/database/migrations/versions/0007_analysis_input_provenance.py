"""add source policy and input version to analysis results

Revision ID: 0007_analysis_input_provenance
Revises: 0006_spillover_model_results
Create Date: 2026-07-12 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_analysis_input_provenance"
down_revision = "0006_spillover_model_results"
branch_labels = None
depends_on = None


TABLES = [
    ("correlation_results", "uq_correlation_result", "uq_correlation_result_input"),
    ("spillover_features", "uq_spillover_feature", "uq_spillover_feature_input"),
    ("spillover_model_results", "uq_spillover_model_result", "uq_spillover_model_result_input"),
]


def upgrade() -> None:
    for table, _, _ in TABLES:
        op.add_column(table, sa.Column("input_data_version", sa.String(length=64), nullable=True))
        op.add_column(table, sa.Column("source_policy_version", sa.String(length=64), nullable=True))
        op.add_column(table, sa.Column("analysis_status", sa.String(length=32), nullable=True))
        op.execute(
            f"UPDATE {table} SET input_data_version = 'legacy-unknown', "
            "source_policy_version = 'legacy-unknown', analysis_status = 'requires_recalculation'"
        )
        op.alter_column(table, "input_data_version", nullable=False)
        op.alter_column(table, "source_policy_version", nullable=False)
        op.alter_column(table, "analysis_status", nullable=False)

    op.drop_constraint("uq_correlation_result", "correlation_results", type_="unique")
    op.create_unique_constraint(
        "uq_correlation_result_input",
        "correlation_results",
        ["analysis_name", "base_symbol", "target_symbol", "window_days", "period_end", "method", "input_data_version"],
    )
    op.drop_constraint("uq_spillover_feature", "spillover_features", type_="unique")
    op.create_unique_constraint(
        "uq_spillover_feature_input",
        "spillover_features",
        ["base_symbol", "target_symbol", "japan_session_date", "metric", "input_data_version"],
    )
    op.drop_constraint("uq_spillover_model_result", "spillover_model_results", type_="unique")
    op.create_unique_constraint(
        "uq_spillover_model_result_input",
        "spillover_model_results",
        [
            "analysis_name",
            "base_symbol",
            "target_symbol",
            "target_metric",
            "window_days",
            "period_end",
            "method",
            "input_data_version",
        ],
    )
    for table, _, _ in TABLES:
        op.create_index(f"ix_{table}_analysis_status", table, ["analysis_status"])


def downgrade() -> None:
    for table, _, new_constraint in TABLES:
        op.drop_index(f"ix_{table}_analysis_status", table_name=table)
        op.drop_constraint(new_constraint, table, type_="unique")

    op.create_unique_constraint(
        "uq_correlation_result",
        "correlation_results",
        ["analysis_name", "base_symbol", "target_symbol", "window_days", "period_end", "method"],
    )
    op.create_unique_constraint(
        "uq_spillover_feature",
        "spillover_features",
        ["base_symbol", "target_symbol", "japan_session_date", "metric"],
    )
    op.create_unique_constraint(
        "uq_spillover_model_result",
        "spillover_model_results",
        ["analysis_name", "base_symbol", "target_symbol", "target_metric", "window_days", "period_end", "method"],
    )
    for table, _, _ in TABLES:
        op.drop_column(table, "analysis_status")
        op.drop_column(table, "source_policy_version")
        op.drop_column(table, "input_data_version")
