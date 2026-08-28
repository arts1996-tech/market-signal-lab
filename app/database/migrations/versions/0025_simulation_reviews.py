"""add append-only deterministic simulation reviews

Revision ID: 0025_simulation_reviews
Revises: 0024_trade_mode_backtests
Create Date: 2026-08-28 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0025_simulation_reviews"
down_revision = "0024_trade_mode_backtests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "simulation_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("review_id", sa.String(64), nullable=False),
        sa.Column("decision_id", sa.String(128), nullable=False),
        sa.Column("source_reference_type", sa.String(64), nullable=False),
        sa.Column("source_reference_id", sa.String(128), nullable=False),
        sa.Column("asset_id", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("horizon", sa.String(32), nullable=False),
        sa.Column("subject", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("decision_mode", sa.String(32), nullable=False),
        sa.Column("execution_mode", sa.String(32), nullable=True),
        sa.Column("data_scope", sa.String(32), nullable=False),
        sa.Column("decision_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("review_version", sa.String(64), nullable=False),
        sa.Column("decision_input_hash", sa.String(64), nullable=False),
        sa.Column("outcome_input_hash", sa.String(64), nullable=False),
        sa.Column("review_input_hash", sa.String(64), nullable=False),
        sa.Column("result_hash", sa.String(64), nullable=False),
        sa.Column("included_in_performance", sa.Boolean(), nullable=False),
        sa.Column("research_only", sa.Boolean(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "subject IN ('executed_trade', 'skipped', 'unfilled')",
            name="ck_simulation_review_subject",
        ),
        sa.CheckConstraint(
            "status IN ('complete', 'insufficient_data')",
            name="ck_simulation_review_status",
        ),
        sa.CheckConstraint(
            "horizon IN ('short_term', 'mid_term')",
            name="ck_simulation_review_horizon",
        ),
        sa.CheckConstraint(
            "data_scope IN ('synthetic_research', 'delayed_historical')",
            name="ck_simulation_review_data_scope",
        ),
        sa.CheckConstraint(
            "decision_mode IN ('cash', 'margin_long', 'margin_short', 'auto_select')",
            name="ck_simulation_review_decision_mode",
        ),
        sa.CheckConstraint(
            "execution_mode IS NULL OR execution_mode IN "
            "('cash', 'margin_long', 'margin_short')",
            name="ck_simulation_review_execution_mode",
        ),
        sa.CheckConstraint(
            "(subject = 'executed_trade' AND included_in_performance = true "
            "AND execution_mode IS NOT NULL) OR "
            "(subject IN ('skipped', 'unfilled') "
            "AND included_in_performance = false)",
            name="ck_simulation_review_performance_scope",
        ),
        sa.CheckConstraint(
            "research_only = true",
            name="ck_simulation_review_research_only",
        ),
        sa.CheckConstraint(
            "decision_at <= outcome_at AND outcome_at <= reviewed_at",
            name="ck_simulation_review_time_order",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("review_id", name="uq_simulation_review_id"),
    )
    op.create_index(
        "ix_simulation_reviews_decision",
        "simulation_reviews",
        ["decision_id", "reviewed_at"],
    )
    op.execute(
        """
        CREATE FUNCTION reject_simulation_review_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'simulation reviews are append-only: % is not allowed', TG_OP;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_simulation_reviews_append_only
        BEFORE UPDATE OR DELETE ON simulation_reviews
        FOR EACH ROW EXECUTE FUNCTION reject_simulation_review_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_simulation_reviews_append_only
        ON simulation_reviews
        """
    )
    op.execute("DROP FUNCTION IF EXISTS reject_simulation_review_mutation()")
    op.drop_index(
        "ix_simulation_reviews_decision",
        table_name="simulation_reviews",
    )
    op.drop_table("simulation_reviews")
