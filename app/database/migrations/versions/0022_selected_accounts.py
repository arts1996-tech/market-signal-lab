"""bind selected-universe forward accounts to immutable selection versions

Revision ID: 0022_selected_accounts
Revises: 0021_selection_claims
Create Date: 2026-08-27 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0022_selected_accounts"
down_revision = "0021_selection_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "virtual_accounts",
        sa.Column(
            "account_scope",
            sa.String(32),
            nullable=False,
            server_default="standard",
        ),
    )
    op.add_column(
        "virtual_accounts",
        sa.Column("allowed_selection_id", postgresql.UUID(as_uuid=False)),
    )
    op.add_column(
        "virtual_accounts", sa.Column("allowed_selection_version", sa.Integer())
    )
    op.add_column(
        "virtual_accounts",
        sa.Column("allowed_selection_composition_hash", sa.String(64)),
    )
    op.add_column(
        "virtual_accounts", sa.Column("selection_change_policy", sa.String(64))
    )
    op.create_foreign_key(
        "fk_virtual_accounts_allowed_selection",
        "virtual_accounts",
        "user_asset_selections",
        ["allowed_selection_id"],
        ["id"],
    )
    op.create_check_constraint(
        "ck_virtual_accounts_scope",
        "virtual_accounts",
        "account_scope IN ('standard', 'selected_universe')",
    )
    op.create_check_constraint(
        "ck_virtual_accounts_selection_scope",
        "virtual_accounts",
        "(account_scope = 'selected_universe' AND allowed_selection_id IS NOT NULL "
        "AND allowed_selection_version IS NOT NULL "
        "AND allowed_selection_composition_hash IS NOT NULL) "
        "OR (account_scope = 'standard' AND allowed_selection_id IS NULL "
        "AND allowed_selection_version IS NULL AND allowed_selection_composition_hash IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_virtual_accounts_selection_scope", "virtual_accounts", type_="check"
    )
    op.drop_constraint("ck_virtual_accounts_scope", "virtual_accounts", type_="check")
    op.drop_constraint(
        "fk_virtual_accounts_allowed_selection", "virtual_accounts", type_="foreignkey"
    )
    op.drop_column("virtual_accounts", "selection_change_policy")
    op.drop_column("virtual_accounts", "allowed_selection_composition_hash")
    op.drop_column("virtual_accounts", "allowed_selection_version")
    op.drop_column("virtual_accounts", "allowed_selection_id")
    op.drop_column("virtual_accounts", "account_scope")
