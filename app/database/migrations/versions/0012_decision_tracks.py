"""separate delayed research and current-market decision tracks

Revision ID: 0012_decision_tracks
Revises: 0011_virtual_account_ledger
Create Date: 2026-08-16 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0012_decision_tracks"
down_revision = "0011_virtual_account_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index(
        "ix_virtual_account_daily_states_lookup",
        table_name="virtual_account_daily_states",
    )
    op.drop_constraint(
        "uq_virtual_account_daily_state_session",
        "virtual_account_daily_states",
        type_="unique",
    )
    op.add_column(
        "virtual_account_daily_states",
        sa.Column(
            "decision_track",
            sa.String(length=32),
            server_default="delayed_historical",
            nullable=False,
        ),
    )
    op.add_column(
        "virtual_account_daily_states",
        sa.Column("price_latest_session", sa.Date(), nullable=True),
    )
    op.add_column(
        "virtual_account_daily_states",
        sa.Column("data_delay_days", sa.Integer(), nullable=True),
    )
    op.add_column(
        "virtual_account_daily_states",
        sa.Column(
            "data_sources",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "virtual_account_daily_states",
        sa.Column(
            "quality_gate_status",
            sa.String(length=32),
            server_default="unclassified",
            nullable=False,
        ),
    )
    op.add_column(
        "virtual_account_daily_states",
        sa.Column(
            "quality_gate_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "virtual_account_daily_states",
        sa.Column(
            "observation_input_hash",
            sa.String(length=64),
            server_default="legacy_untracked",
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_virtual_account_daily_state_track_session",
        "virtual_account_daily_states",
        ["account_id", "decision_track", "session_date"],
    )
    op.create_index(
        "ix_virtual_account_daily_states_lookup",
        "virtual_account_daily_states",
        ["account_id", "decision_track", "session_date"],
        unique=False,
    )

    op.drop_index(
        "ix_virtual_account_events_lookup", table_name="virtual_account_events"
    )
    op.add_column(
        "virtual_account_events",
        sa.Column(
            "decision_track",
            sa.String(length=32),
            server_default="delayed_historical",
            nullable=False,
        ),
    )
    op.create_index(
        "ix_virtual_account_events_lookup",
        "virtual_account_events",
        ["account_id", "decision_track", "session_date", "event_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_virtual_account_events_lookup", table_name="virtual_account_events"
    )
    op.drop_column("virtual_account_events", "decision_track")
    op.create_index(
        "ix_virtual_account_events_lookup",
        "virtual_account_events",
        ["account_id", "session_date", "event_type"],
        unique=False,
    )

    op.drop_index(
        "ix_virtual_account_daily_states_lookup",
        table_name="virtual_account_daily_states",
    )
    op.drop_constraint(
        "uq_virtual_account_daily_state_track_session",
        "virtual_account_daily_states",
        type_="unique",
    )
    for column_name in (
        "observation_input_hash",
        "quality_gate_reasons",
        "quality_gate_status",
        "data_sources",
        "data_delay_days",
        "price_latest_session",
        "decision_track",
    ):
        op.drop_column("virtual_account_daily_states", column_name)
    op.create_unique_constraint(
        "uq_virtual_account_daily_state_session",
        "virtual_account_daily_states",
        ["account_id", "session_date"],
    )
    op.create_index(
        "ix_virtual_account_daily_states_lookup",
        "virtual_account_daily_states",
        ["account_id", "session_date"],
        unique=False,
    )
