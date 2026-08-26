"""add append-only margin trading snapshots

Revision ID: 0023_margin_snapshots
Revises: 0022_selected_accounts
Create Date: 2026-08-27 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0023_margin_snapshots"
down_revision = "0022_selected_accounts"
branch_labels = None
depends_on = None


QUALITY_VALUES = (
    "data_quality_status IN ('verified', 'partial', 'stale', "
    "'unavailable', 'synthetic_research')"
)


def upgrade() -> None:
    op.create_table(
        "asset_trading_capabilities",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("provider_record_id", sa.String(128), nullable=False),
        sa.Column("market", sa.String(8), nullable=False),
        sa.Column("asset_type", sa.String(16), nullable=False),
        sa.Column("broker_scope", sa.String(64), nullable=False),
        sa.Column("margin_long_eligible", sa.Boolean()),
        sa.Column("margin_short_eligible", sa.Boolean()),
        sa.Column("credit_types", postgresql.JSONB(), nullable=False),
        sa.Column("is_lending_issue", sa.Boolean()),
        sa.Column("short_availability", sa.String(32), nullable=False),
        sa.Column("restriction_codes", postgresql.JSONB(), nullable=False),
        sa.Column("repayment_term_days", sa.Integer()),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True)),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_version", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("data_quality_status", sa.String(32), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "market IN ('jp', 'us')", name="ck_asset_trading_capability_market"
        ),
        sa.CheckConstraint(
            "asset_type IN ('stock', 'etf')",
            name="ck_asset_trading_capability_asset_type",
        ),
        sa.CheckConstraint(
            "short_availability IN ('available', 'limited', 'unavailable', "
            "'unknown', 'not_applicable')",
            name="ck_asset_trading_capability_short_availability",
        ),
        sa.CheckConstraint(QUALITY_VALUES, name="ck_asset_trading_capability_quality"),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_asset_trading_capability_effective_period",
        ),
        sa.CheckConstraint(
            "repayment_term_days IS NULL OR repayment_term_days > 0",
            name="ck_asset_trading_capability_repayment_term",
        ),
        sa.CheckConstraint(
            "fetched_at >= available_at",
            name="ck_asset_trading_capability_fetch_time",
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source",
            "provider_record_id",
            "fetched_at",
            name="uq_asset_trading_capability_provider_record",
        ),
    )
    op.create_index(
        "ix_asset_trading_capabilities_lookup",
        "asset_trading_capabilities",
        ["asset_id", "broker_scope", "effective_from", "available_at"],
    )

    op.create_table(
        "margin_market_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("provider_record_id", sa.String(128), nullable=False),
        sa.Column("market", sa.String(8), nullable=False),
        sa.Column("broker_scope", sa.String(64), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("session_date", sa.Date()),
        sa.Column("margin_long_balance", sa.Numeric(24, 4)),
        sa.Column("margin_short_balance", sa.Numeric(24, 4)),
        sa.Column("lending_ratio", sa.Numeric(18, 8)),
        sa.Column("reverse_stock_borrow_fee", sa.Numeric(18, 8)),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_version", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("data_quality_status", sa.String(32), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "market IN ('jp', 'us')", name="ck_margin_market_snapshot_market"
        ),
        sa.CheckConstraint(
            "margin_long_balance IS NULL OR margin_long_balance >= 0",
            name="ck_margin_market_snapshot_long_balance",
        ),
        sa.CheckConstraint(
            "margin_short_balance IS NULL OR margin_short_balance >= 0",
            name="ck_margin_market_snapshot_short_balance",
        ),
        sa.CheckConstraint(
            "lending_ratio IS NULL OR lending_ratio >= 0",
            name="ck_margin_market_snapshot_lending_ratio",
        ),
        sa.CheckConstraint(
            "reverse_stock_borrow_fee IS NULL OR reverse_stock_borrow_fee >= 0",
            name="ck_margin_market_snapshot_reverse_fee",
        ),
        sa.CheckConstraint(QUALITY_VALUES, name="ck_margin_market_snapshot_quality"),
        sa.CheckConstraint(
            "fetched_at >= available_at",
            name="ck_margin_market_snapshot_fetch_time",
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source",
            "provider_record_id",
            "fetched_at",
            name="uq_margin_market_snapshot_provider_record",
        ),
    )
    op.create_index(
        "ix_margin_market_snapshots_lookup",
        "margin_market_snapshots",
        ["asset_id", "session_date", "available_at"],
    )

    op.create_table(
        "financing_term_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("provider_record_id", sa.String(128), nullable=False),
        sa.Column("market", sa.String(8), nullable=False),
        sa.Column("broker_scope", sa.String(64), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("margin_interest_rate", sa.Numeric(18, 8)),
        sa.Column("stock_lending_fee", sa.Numeric(18, 8)),
        sa.Column("borrow_cost", sa.Numeric(18, 8)),
        sa.Column("initial_margin_rate", sa.Numeric(18, 8)),
        sa.Column("maintenance_margin_rate", sa.Numeric(18, 8)),
        sa.Column("minimum_margin_amount", sa.Numeric(20, 4)),
        sa.Column("repayment_term_days", sa.Integer()),
        sa.Column("forced_liquidation_rule_version", sa.String(64)),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True)),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_version", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("data_quality_status", sa.String(32), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "market IN ('jp', 'us')", name="ck_financing_term_snapshot_market"
        ),
        sa.CheckConstraint(
            "initial_margin_rate IS NULL OR "
            "(initial_margin_rate >= 0 AND initial_margin_rate <= 1)",
            name="ck_financing_term_snapshot_initial_margin",
        ),
        sa.CheckConstraint(
            "maintenance_margin_rate IS NULL OR "
            "(maintenance_margin_rate >= 0 AND maintenance_margin_rate <= 1)",
            name="ck_financing_term_snapshot_maintenance_margin",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_financing_term_snapshot_effective_period",
        ),
        sa.CheckConstraint(
            "margin_interest_rate IS NULL OR margin_interest_rate >= 0",
            name="ck_financing_term_snapshot_interest",
        ),
        sa.CheckConstraint(
            "stock_lending_fee IS NULL OR stock_lending_fee >= 0",
            name="ck_financing_term_snapshot_lending_fee",
        ),
        sa.CheckConstraint(
            "borrow_cost IS NULL OR borrow_cost >= 0",
            name="ck_financing_term_snapshot_borrow_cost",
        ),
        sa.CheckConstraint(
            "minimum_margin_amount IS NULL OR minimum_margin_amount >= 0",
            name="ck_financing_term_snapshot_minimum_margin",
        ),
        sa.CheckConstraint(
            "repayment_term_days IS NULL OR repayment_term_days > 0",
            name="ck_financing_term_snapshot_repayment_term",
        ),
        sa.CheckConstraint(QUALITY_VALUES, name="ck_financing_term_snapshot_quality"),
        sa.CheckConstraint(
            "fetched_at >= available_at",
            name="ck_financing_term_snapshot_fetch_time",
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source",
            "provider_record_id",
            "fetched_at",
            name="uq_financing_term_snapshot_provider_record",
        ),
    )
    op.create_index(
        "ix_financing_term_snapshots_lookup",
        "financing_term_snapshots",
        ["asset_id", "broker_scope", "effective_from", "available_at"],
    )

    op.execute(
        """
        CREATE FUNCTION reject_margin_snapshot_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'margin snapshot history is append-only: % is not allowed', TG_OP;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table_name in (
        "asset_trading_capabilities",
        "margin_market_snapshots",
        "financing_term_snapshots",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_append_only
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION reject_margin_snapshot_mutation()
            """
        )


def downgrade() -> None:
    for table_name in (
        "financing_term_snapshots",
        "margin_market_snapshots",
        "asset_trading_capabilities",
    ):
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table_name}_append_only ON {table_name}"
        )
    op.execute("DROP FUNCTION IF EXISTS reject_margin_snapshot_mutation()")
    op.drop_index(
        "ix_financing_term_snapshots_lookup",
        table_name="financing_term_snapshots",
    )
    op.drop_table("financing_term_snapshots")
    op.drop_index(
        "ix_margin_market_snapshots_lookup",
        table_name="margin_market_snapshots",
    )
    op.drop_table("margin_market_snapshots")
    op.drop_index(
        "ix_asset_trading_capabilities_lookup",
        table_name="asset_trading_capabilities",
    )
    op.drop_table("asset_trading_capabilities")
