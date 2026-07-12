"""add phase 1 price provenance and quality fields

Revision ID: 0003_price_provenance
Revises: 0002_correlation_results
Create Date: 2026-07-11 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_price_provenance"
down_revision = "0002_correlation_results"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("market_prices", sa.Column("session_date", sa.Date(), nullable=True))
    op.add_column("market_prices", sa.Column("source_symbol", sa.String(length=64), nullable=True))
    op.add_column("market_prices", sa.Column("available_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "market_prices",
        sa.Column(
            "data_quality_status",
            sa.String(length=32),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.execute("UPDATE market_prices SET session_date = CAST(price_time AS date)")
    op.execute("UPDATE market_prices SET available_at = fetched_at")
    op.execute(
        """
        UPDATE market_prices AS prices
        SET source_symbol = assets.symbol
        FROM assets
        WHERE prices.asset_id = assets.id
        """
    )
    op.alter_column("market_prices", "session_date", nullable=False)
    op.alter_column("market_prices", "source_symbol", nullable=False)
    op.alter_column("market_prices", "available_at", nullable=False)
    op.alter_column("market_prices", "data_quality_status", server_default=None)


def downgrade() -> None:
    op.drop_column("market_prices", "data_quality_status")
    op.drop_column("market_prices", "available_at")
    op.drop_column("market_prices", "source_symbol")
    op.drop_column("market_prices", "session_date")
