"""add resumable J-Quants price collection progress

Revision ID: 0004_price_collection_progress
Revises: 0003_price_provenance
Create Date: 2026-07-11 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0004_price_collection_progress"
down_revision = "0003_price_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "price_collection_targets",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "session_date", name="uq_price_collection_target"),
    )
    op.create_table(
        "price_collection_items",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "asset_id", "session_date", name="uq_price_collection_item"),
    )
    op.create_index(
        "ix_price_collection_items_lookup",
        "price_collection_items",
        ["source", "session_date", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_price_collection_items_lookup", table_name="price_collection_items")
    op.drop_table("price_collection_items")
    op.drop_table("price_collection_targets")
