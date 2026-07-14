"""add optional SEC CIK mapping to assets

Revision ID: 0010_asset_sec_cik
Revises: 0009_phase3_fundamentals_etf
Create Date: 2026-07-14 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_asset_sec_cik"
down_revision = "0009_phase3_fundamentals_etf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assets", sa.Column("sec_cik", sa.String(length=10), nullable=True))
    op.create_unique_constraint("uq_assets_sec_cik", "assets", ["sec_cik"])


def downgrade() -> None:
    op.drop_constraint("uq_assets_sec_cik", "assets", type_="unique")
    op.drop_column("assets", "sec_cik")
