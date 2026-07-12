"""add explicit market price basis

Revision ID: 0008_market_price_basis
Revises: 0007_analysis_input_provenance
Create Date: 2026-07-12 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_market_price_basis"
down_revision = "0007_analysis_input_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("market_prices", sa.Column("price_basis", sa.String(length=64), nullable=True))
    op.add_column("market_prices", sa.Column("adjusted_open", sa.Numeric(precision=18, scale=6), nullable=True))
    op.add_column("market_prices", sa.Column("adjusted_high", sa.Numeric(precision=18, scale=6), nullable=True))
    op.add_column("market_prices", sa.Column("adjusted_low", sa.Numeric(precision=18, scale=6), nullable=True))
    op.add_column("market_prices", sa.Column("adjusted_volume", sa.Numeric(precision=24, scale=2), nullable=True))
    op.add_column("market_prices", sa.Column("adjustment_factor", sa.Numeric(precision=18, scale=8), nullable=True))
    op.execute("UPDATE market_prices SET price_basis = 'legacy_unknown'")
    op.alter_column("market_prices", "price_basis", nullable=False)
    for table in ["correlation_results", "spillover_features", "spillover_model_results"]:
        op.execute(
            f"UPDATE {table} SET analysis_status = 'requires_recalculation' "
            "WHERE analysis_status = 'current'"
        )


def downgrade() -> None:
    op.drop_column("market_prices", "adjustment_factor")
    op.drop_column("market_prices", "adjusted_volume")
    op.drop_column("market_prices", "adjusted_low")
    op.drop_column("market_prices", "adjusted_high")
    op.drop_column("market_prices", "adjusted_open")
    op.drop_column("market_prices", "price_basis")
