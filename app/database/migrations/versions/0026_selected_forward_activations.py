"""add explicit append-only selected forward activation events

Revision ID: 0026_selected_forward
Revises: 0025_simulation_reviews
Create Date: 2026-09-02 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0026_selected_forward"
down_revision = "0025_simulation_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "selected_universe_forward_activation_events",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("selection_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_by", sa.String(100), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("activation_version", sa.String(64), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["selection_id"], ["user_asset_selections.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "request_id", name="uq_selected_forward_activation_request"
        ),
    )
    op.create_index(
        "ix_selected_forward_activation_latest",
        "selected_universe_forward_activation_events",
        ["selection_id", "requested_at", "created_at"],
    )
    op.execute(
        """
        CREATE FUNCTION reject_selected_forward_activation_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'selected forward activation events are append-only: % is not allowed', TG_OP;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_selected_forward_activation_append_only
        BEFORE UPDATE OR DELETE ON selected_universe_forward_activation_events
        FOR EACH ROW EXECUTE FUNCTION reject_selected_forward_activation_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_selected_forward_activation_append_only
        ON selected_universe_forward_activation_events
        """
    )
    op.execute("DROP FUNCTION IF EXISTS reject_selected_forward_activation_mutation()")
    op.drop_index(
        "ix_selected_forward_activation_latest",
        table_name="selected_universe_forward_activation_events",
    )
    op.drop_table("selected_universe_forward_activation_events")
