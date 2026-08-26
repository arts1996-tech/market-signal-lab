"""add versioned theme definitions and asset memberships

Revision ID: 0017_theme_definitions
Revises: 0016_nullable_fx_valuation
Create Date: 2026-08-26 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0017_theme_definitions"
down_revision = "0016_nullable_fx_valuation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("themes", sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False), sa.Column("identifier", sa.String(64), nullable=False), sa.Column("name", sa.String(100), nullable=False), sa.Column("status", sa.String(32), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("identifier"))
    op.create_table("theme_versions", sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False), sa.Column("theme_id", postgresql.UUID(as_uuid=False), nullable=False), sa.Column("baseline_tier", sa.String(16), nullable=False), sa.Column("enabled", sa.Boolean(), nullable=False), sa.Column("margin_trading_enabled", sa.Boolean(), nullable=False), sa.Column("description", sa.Text(), nullable=False), sa.Column("effective_from", sa.Date(), nullable=False), sa.Column("effective_to", sa.Date()), sa.Column("definition_version", sa.String(64), nullable=False), sa.Column("composition_hash", sa.String(64), nullable=False), sa.Column("status", sa.String(32), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.ForeignKeyConstraint(["theme_id"], ["themes.id"]), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("theme_id", "composition_hash", name="uq_theme_version_composition"))
    op.create_index("ix_theme_versions_effective", "theme_versions", ["theme_id", "effective_from"])
    op.create_table("theme_asset_memberships", sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False), sa.Column("theme_version_id", postgresql.UUID(as_uuid=False), nullable=False), sa.Column("asset_id", postgresql.UUID(as_uuid=False), nullable=False), sa.Column("role", sa.String(32), nullable=False), sa.Column("effective_from", sa.Date(), nullable=False), sa.Column("effective_to", sa.Date()), sa.Column("source_reference", sa.String(500), nullable=False), sa.Column("notes", sa.Text(), nullable=False), sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]), sa.ForeignKeyConstraint(["theme_version_id"], ["theme_versions.id"]), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("theme_version_id", "asset_id", "role", "effective_from", name="uq_theme_asset_membership"))
    op.create_index("ix_theme_asset_memberships_asset", "theme_asset_memberships", ["asset_id", "effective_from"])


def downgrade() -> None:
    op.drop_index("ix_theme_asset_memberships_asset", table_name="theme_asset_memberships")
    op.drop_table("theme_asset_memberships")
    op.drop_index("ix_theme_versions_effective", table_name="theme_versions")
    op.drop_table("theme_versions")
    op.drop_table("themes")
