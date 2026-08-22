"""allow deferred FX valuation in virtual account daily states

Revision ID: 0016_nullable_fx_valuation
Revises: 0015_asset_lifecycle
Create Date: 2026-08-22 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0016_nullable_fx_valuation"
down_revision = "0015_asset_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column, type_ in (
        ("equity", sa.Numeric(20, 4)),
        ("unrealized_pnl", sa.Numeric(20, 4)),
        ("cumulative_pnl", sa.Numeric(20, 4)),
        ("maximum_drawdown", sa.Numeric(12, 8)),
    ):
        op.alter_column(
            "virtual_account_daily_states",
            column,
            existing_type=type_,
            nullable=True,
        )


def downgrade() -> None:
    for column, type_ in (
        ("equity", sa.Numeric(20, 4)),
        ("unrealized_pnl", sa.Numeric(20, 4)),
        ("cumulative_pnl", sa.Numeric(20, 4)),
        ("maximum_drawdown", sa.Numeric(12, 8)),
    ):
        op.alter_column(
            "virtual_account_daily_states",
            column,
            existing_type=type_,
            nullable=False,
        )
