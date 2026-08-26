"""add immutable user-selected asset collection versions

Revision ID: 0018_user_asset_selections
Revises: 0017_theme_definitions
Create Date: 2026-08-26 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0018_user_asset_selections"
down_revision = "0017_theme_definitions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_asset_selections",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("selection_key", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("composition_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('active', 'inactive')", name="ck_user_asset_selection_status"),
        sa.CheckConstraint("version > 0", name="ck_user_asset_selection_version_positive"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("selection_key", "version", name="uq_user_asset_selection_version"),
    )
    op.create_index(
        "ix_user_asset_selections_effective",
        "user_asset_selections",
        ["selection_key", "effective_from"],
    )
    op.create_table(
        "user_asset_selection_items",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("selection_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "display_order > 0", name="ck_user_asset_selection_item_order_positive"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'inactive')", name="ck_user_asset_selection_item_status"
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.ForeignKeyConstraint(["selection_id"], ["user_asset_selections.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("selection_id", "asset_id", name="uq_user_asset_selection_item_asset"),
        sa.UniqueConstraint(
            "selection_id", "display_order", name="uq_user_asset_selection_item_order"
        ),
    )
    op.create_index(
        "ix_user_asset_selection_items_asset", "user_asset_selection_items", ["asset_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_user_asset_selection_items_asset", table_name="user_asset_selection_items")
    op.drop_table("user_asset_selection_items")
    op.drop_index("ix_user_asset_selections_effective", table_name="user_asset_selections")
    op.drop_table("user_asset_selections")
