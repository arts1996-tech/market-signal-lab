from datetime import date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def uuid_pk() -> str:
    return str(uuid4())


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="JPY")
    exchange: Mapped[str | None] = mapped_column(String(64))
    sec_cik: Mapped[str | None] = mapped_column(String(10), unique=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    prices: Mapped[list["MarketPrice"]] = relationship(back_populates="asset")


class MarketPrice(Base):
    __tablename__ = "market_prices"
    __table_args__ = (
        UniqueConstraint("asset_id", "timeframe", "price_time", "source", name="uq_market_price"),
        Index("ix_market_prices_asset_time", "asset_id", "price_time"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    asset_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("assets.id"), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False, default="1d")
    price_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[float | None] = mapped_column(Numeric(18, 6))
    high: Mapped[float | None] = mapped_column(Numeric(18, 6))
    low: Mapped[float | None] = mapped_column(Numeric(18, 6))
    close: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    adjusted_close: Mapped[float | None] = mapped_column(Numeric(18, 6))
    adjusted_open: Mapped[float | None] = mapped_column(Numeric(18, 6))
    adjusted_high: Mapped[float | None] = mapped_column(Numeric(18, 6))
    adjusted_low: Mapped[float | None] = mapped_column(Numeric(18, 6))
    adjusted_volume: Mapped[float | None] = mapped_column(Numeric(24, 2))
    adjustment_factor: Mapped[float | None] = mapped_column(Numeric(18, 8))
    volume: Mapped[float | None] = mapped_column(Numeric(24, 2))
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_quality_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    price_basis: Mapped[str] = mapped_column(String(64), nullable=False, default="legacy_unknown")

    asset: Mapped[Asset] = relationship(back_populates="prices")


class FundamentalSnapshot(Base):
    __tablename__ = "fundamental_snapshots"
    __table_args__ = (
        UniqueConstraint("asset_id", "disclosed_at", "period_end", "source", name="uq_fundamental_snapshot"),
        Index("ix_fundamental_snapshots_asset_period", "asset_id", "period_end"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    asset_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("assets.id"), nullable=False)
    disclosed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    sales: Mapped[float | None] = mapped_column(Numeric(24, 6))
    operating_profit: Mapped[float | None] = mapped_column(Numeric(24, 6))
    net_income: Mapped[float | None] = mapped_column(Numeric(24, 6))
    eps: Mapped[float | None] = mapped_column(Numeric(18, 8))
    equity: Mapped[float | None] = mapped_column(Numeric(24, 6))
    total_assets: Mapped[float | None] = mapped_column(Numeric(24, 6))
    operating_cashflow: Mapped[float | None] = mapped_column(Numeric(24, 6))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class EtfMetricSnapshot(Base):
    __tablename__ = "etf_metric_snapshots"
    __table_args__ = (
        UniqueConstraint("asset_id", "observed_at", "source", name="uq_etf_metric_snapshot"),
        Index("ix_etf_metric_snapshots_asset_observed", "asset_id", "observed_at"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    asset_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("assets.id"), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CorporateAction(Base):
    __tablename__ = "corporate_actions"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "source_event_id",
            "fetched_at",
            name="uq_corporate_action_source_event",
        ),
        CheckConstraint(
            "action_type IN ('stock_split', 'reverse_split', 'cash_dividend', 'merger', 'share_exchange')",
            name="ck_corporate_action_type",
        ),
        CheckConstraint(
            "status IN ('confirmed', 'pending', 'cancelled')",
            name="ck_corporate_action_status",
        ),
        CheckConstraint(
            "(action_type != 'stock_split' OR (ratio IS NOT NULL AND ratio > 1)) AND "
            "(action_type != 'reverse_split' OR (ratio IS NOT NULL AND ratio > 0 AND ratio < 1))",
            name="ck_corporate_action_ratio",
        ),
        CheckConstraint(
            "action_type != 'cash_dividend' OR "
            "(ex_date IS NOT NULL AND record_date IS NOT NULL AND payable_date IS NOT NULL "
            "AND ex_date <= record_date AND record_date <= payable_date "
            "AND cash_per_share IS NOT NULL AND cash_per_share >= 0 AND currency IS NOT NULL)",
            name="ck_corporate_action_dividend_terms",
        ),
        Index("ix_corporate_actions_asset_effective", "asset_id", "effective_date"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    asset_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("assets.id"), nullable=False)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    announced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    ex_date: Mapped[date | None] = mapped_column(Date)
    record_date: Mapped[date | None] = mapped_column(Date)
    payable_date: Mapped[date | None] = mapped_column(Date)
    ratio: Mapped[float | None] = mapped_column(Numeric(18, 8))
    cash_per_share: Mapped[float | None] = mapped_column(Numeric(18, 8))
    currency: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class CorporateActionCoverage(Base):
    __tablename__ = "corporate_action_coverages"
    __table_args__ = (
        UniqueConstraint(
            "asset_id",
            "period_start",
            "period_end",
            "source",
            "checked_at",
            name="uq_corporate_action_coverage_period",
        ),
        CheckConstraint(
            "period_start <= period_end",
            name="ck_corporate_action_coverage_period",
        ),
        CheckConstraint(
            "status IN ('complete', 'partial', 'unavailable')",
            name="ck_corporate_action_coverage_status",
        ),
        Index(
            "ix_corporate_action_coverages_asset_period",
            "asset_id",
            "period_start",
            "period_end",
        ),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    asset_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("assets.id"), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class AssetLifecycleRecord(Base):
    __tablename__ = "asset_lifecycle_records"
    __table_args__ = (
        UniqueConstraint(
            "asset_id", "effective_from", "source", "fetched_at",
            name="uq_asset_lifecycle_revision",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_from <= effective_to",
            name="ck_asset_lifecycle_effective_period",
        ),
        CheckConstraint(
            "delisted_on IS NULL OR listed_on IS NULL OR listed_on <= delisted_on",
            name="ck_asset_lifecycle_listing_period",
        ),
        CheckConstraint(
            "investability_status IN ('investable', 'non_investable', 'suspended', 'delisted', 'unknown')",
            name="ck_asset_lifecycle_status",
        ),
        Index("ix_asset_lifecycle_asset_effective", "asset_id", "effective_from"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    asset_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("assets.id"), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    listed_on: Mapped[date | None] = mapped_column(Date)
    delisted_on: Mapped[date | None] = mapped_column(Date)
    market: Mapped[str | None] = mapped_column(String(128))
    sector_17: Mapped[str | None] = mapped_column(String(128))
    sector_33: Mapped[str | None] = mapped_column(String(128))
    investability_status: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class AssetUniverseCoverage(Base):
    __tablename__ = "asset_universe_coverages"
    __table_args__ = (
        UniqueConstraint(
            "period_start", "period_end", "source", "checked_at",
            name="uq_asset_universe_coverage_revision",
        ),
        CheckConstraint(
            "period_start <= period_end", name="ck_asset_universe_coverage_period"
        ),
        CheckConstraint(
            "status IN ('complete', 'partial', 'unavailable')",
            name="ck_asset_universe_coverage_status",
        ),
        CheckConstraint(
            "observed_asset_count IS NULL OR observed_asset_count >= 0",
            name="ck_asset_universe_coverage_count",
        ),
        Index("ix_asset_universe_coverage_period", "period_start", "period_end"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_asset_count: Mapped[int | None] = mapped_column(Integer)
    input_hash: Mapped[str | None] = mapped_column(String(64))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class ApiFetchLog(Base):
    __tablename__ = "api_fetch_logs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_symbol: Mapped[str | None] = mapped_column(String(32))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    latency_ms: Mapped[int | None]
    message: Mapped[str | None] = mapped_column(Text)


class CorrelationResult(Base):
    __tablename__ = "correlation_results"
    __table_args__ = (
        UniqueConstraint(
            "analysis_name",
            "base_symbol",
            "target_symbol",
            "window_days",
            "period_end",
            "method",
            "input_data_version",
            name="uq_correlation_result_input",
        ),
        Index("ix_correlation_results_lookup", "analysis_name", "base_symbol", "target_symbol", "period_end"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    analysis_name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    target_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    window_days: Mapped[int] = mapped_column(Integer, nullable=False)
    method: Mapped[str] = mapped_column(String(64), nullable=False, default="pearson")
    lag_rule: Mapped[str] = mapped_column(String(128), nullable=False)
    correlation: Mapped[float | None] = mapped_column(Numeric(12, 8))
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="market_prices")
    input_data_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    analysis_status: Mapped[str] = mapped_column(String(32), nullable=False, default="current")
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class SpilloverFeature(Base):
    __tablename__ = "spillover_features"
    __table_args__ = (
        UniqueConstraint(
            "base_symbol",
            "target_symbol",
            "japan_session_date",
            "metric",
            "input_data_version",
            name="uq_spillover_feature_input",
        ),
        Index("ix_spillover_features_lookup", "base_symbol", "target_symbol", "japan_session_date"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    base_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    target_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    japan_session_date: Mapped[date] = mapped_column(Date, nullable=False)
    us_session_date: Mapped[date] = mapped_column(Date, nullable=False)
    metric: Mapped[str] = mapped_column(String(32), nullable=False)
    us_return: Mapped[float] = mapped_column(Numeric(12, 8), nullable=False)
    target_return: Mapped[float] = mapped_column(Numeric(12, 8), nullable=False)
    lag_rule: Mapped[str] = mapped_column(String(128), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    input_data_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    analysis_status: Mapped[str] = mapped_column(String(32), nullable=False, default="current")
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class SpilloverModelResult(Base):
    __tablename__ = "spillover_model_results"
    __table_args__ = (
        UniqueConstraint(
            "analysis_name",
            "base_symbol",
            "target_symbol",
            "target_metric",
            "window_days",
            "period_end",
            "method",
            "input_data_version",
            name="uq_spillover_model_result_input",
        ),
        Index(
            "ix_spillover_model_results_lookup",
            "base_symbol",
            "target_symbol",
            "target_metric",
            "period_end",
        ),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    analysis_name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    target_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    target_metric: Mapped[str] = mapped_column(String(32), nullable=False)
    window_days: Mapped[int] = mapped_column(Integer, nullable=False)
    method: Mapped[str] = mapped_column(String(64), nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    r_squared: Mapped[float | None] = mapped_column(Numeric(12, 8))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    model_version: Mapped[str] = mapped_column(String(32), nullable=False)
    input_data_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    analysis_status: Mapped[str] = mapped_column(String(32), nullable=False, default="current")
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    job_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class AssetAnalysisRun(Base):
    __tablename__ = "asset_analysis_runs"
    __table_args__ = (
        UniqueConstraint(
            "analysis_name",
            "data_scope",
            "rule_version",
            "input_data_version",
            name="uq_asset_analysis_run_input",
        ),
        Index(
            "ix_asset_analysis_runs_latest",
            "analysis_name",
            "data_scope",
            "status",
            "completed_at",
        ),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    analysis_name: Mapped[str] = mapped_column(String(64), nullable=False)
    data_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_data_version: Mapped[str] = mapped_column(String(64), nullable=False)
    data_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    assets_considered: Mapped[int] = mapped_column(Integer, nullable=False)
    eligible_asset_count: Mapped[int] = mapped_column(Integer, nullable=False)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class AssetAnalysisResult(Base):
    __tablename__ = "asset_analysis_results"
    __table_args__ = (
        UniqueConstraint("run_id", "asset_id", name="uq_asset_analysis_result_asset"),
        Index("ix_asset_analysis_results_attention", "run_id", "attention_rank"),
        Index("ix_asset_analysis_results_movement", "run_id", "movement_rank"),
        Index(
            "ix_asset_analysis_results_filter",
            "run_id",
            "asset_type",
            "sector",
            "attention_score",
        ),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("asset_analysis_runs.id"), nullable=False
    )
    asset_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("assets.id"), nullable=False
    )
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    sector: Mapped[str] = mapped_column(String(128), nullable=False)
    data_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observations: Mapped[int] = mapped_column(Integer, nullable=False)
    attention_score: Mapped[int] = mapped_column(Integer, nullable=False)
    movement_score: Mapped[int | None] = mapped_column(Integer)
    attention_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    movement_rank: Mapped[int | None] = mapped_column(Integer)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Theme(Base):
    __tablename__ = "themes"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    identifier: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ThemeVersion(Base):
    __tablename__ = "theme_versions"
    __table_args__ = (
        UniqueConstraint("theme_id", "composition_hash", name="uq_theme_version_composition"),
        Index("ix_theme_versions_effective", "theme_id", "effective_from"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    theme_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("themes.id"), nullable=False)
    baseline_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    margin_trading_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    definition_version: Mapped[str] = mapped_column(String(64), nullable=False)
    composition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="approved")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ThemeAssetMembership(Base):
    __tablename__ = "theme_asset_memberships"
    __table_args__ = (
        UniqueConstraint("theme_version_id", "asset_id", "role", "effective_from", name="uq_theme_asset_membership"),
        Index("ix_theme_asset_memberships_asset", "asset_id", "effective_from"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    theme_version_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("theme_versions.id"), nullable=False)
    asset_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("assets.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    source_reference: Mapped[str] = mapped_column(String(500), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")


class UserAssetSelection(Base):
    __tablename__ = "user_asset_selections"
    __table_args__ = (
        UniqueConstraint("selection_key", "version", name="uq_user_asset_selection_version"),
        CheckConstraint("version > 0", name="ck_user_asset_selection_version_positive"),
        CheckConstraint(
            "status IN ('active', 'inactive')", name="ck_user_asset_selection_status"
        ),
        Index("ix_user_asset_selections_effective", "selection_key", "effective_from"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    selection_key: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    composition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserAssetSelectionItem(Base):
    __tablename__ = "user_asset_selection_items"
    __table_args__ = (
        UniqueConstraint("selection_id", "asset_id", name="uq_user_asset_selection_item_asset"),
        UniqueConstraint(
            "selection_id", "display_order", name="uq_user_asset_selection_item_order"
        ),
        CheckConstraint("display_order > 0", name="ck_user_asset_selection_item_order_positive"),
        CheckConstraint(
            "status IN ('active', 'inactive')", name="ck_user_asset_selection_item_status"
        ),
        Index("ix_user_asset_selection_items_asset", "asset_id"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    selection_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("user_asset_selections.id"), nullable=False
    )
    asset_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("assets.id"), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserAssetSelectionAnalysisRun(Base):
    __tablename__ = "user_asset_selection_analysis_runs"
    __table_args__ = (
        UniqueConstraint(
            "selection_id",
            "source_asset_analysis_run_id",
            name="uq_user_selection_analysis_source",
        ),
        CheckConstraint(
            "status IN ('success', 'partial', 'insufficient_data')",
            name="ck_user_selection_analysis_run_status",
        ),
        Index(
            "ix_user_selection_analysis_runs_latest",
            "selection_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    selection_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("user_asset_selections.id"), nullable=False
    )
    selection_key: Mapped[str] = mapped_column(String(64), nullable=False)
    selection_version: Mapped[int] = mapped_column(Integer, nullable=False)
    selection_composition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_asset_analysis_run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("asset_analysis_runs.id"), nullable=False
    )
    data_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    analysis_rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_data_version: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    data_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserAssetSelectionAnalysisResult(Base):
    __tablename__ = "user_asset_selection_analysis_results"
    __table_args__ = (
        UniqueConstraint("run_id", "asset_id", name="uq_user_selection_analysis_result_asset"),
        CheckConstraint(
            "analysis_status IN ('analyzed', 'insufficient_data')",
            name="ck_user_selection_analysis_result_status",
        ),
        Index("ix_user_selection_analysis_results_status", "run_id", "analysis_status"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("user_asset_selection_analysis_runs.id"), nullable=False
    )
    asset_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("assets.id"), nullable=False)
    source_asset_analysis_result_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("asset_analysis_results.id")
    )
    analysis_status: Mapped[str] = mapped_column(String(32), nullable=False)
    data_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observations: Mapped[int | None] = mapped_column(Integer)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    quality_reasons: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SelectedUniverseBacktestRun(Base):
    __tablename__ = "selected_universe_backtest_runs"
    __table_args__ = (
        UniqueConstraint(
            "analysis_snapshot_run_id",
            "horizon",
            "simulation_hash",
            name="uq_selected_universe_backtest_input",
        ),
        CheckConstraint(
            "horizon IN ('short_term', 'mid_term')", name="ck_selected_universe_backtest_horizon"
        ),
        CheckConstraint("trade_mode = 'cash'", name="ck_selected_universe_backtest_cash_only"),
        CheckConstraint(
            "status IN ('success', 'insufficient_data')",
            name="ck_selected_universe_backtest_status",
        ),
        Index("ix_selected_universe_backtest_latest", "selection_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    analysis_snapshot_run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("user_asset_selection_analysis_runs.id"), nullable=False
    )
    selection_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("user_asset_selections.id"), nullable=False
    )
    selection_key: Mapped[str] = mapped_column(String(64), nullable=False)
    selection_version: Mapped[int] = mapped_column(Integer, nullable=False)
    selection_composition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(64), nullable=False, default="selected_universe_portfolio")
    horizon: Mapped[str] = mapped_column(String(32), nullable=False)
    trade_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="cash")
    initial_cash: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_version: Mapped[str] = mapped_column(String(64), nullable=False)
    data_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    input_data_version: Mapped[str] = mapped_column(String(64), nullable=False)
    simulation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reasons: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SelectedUniverseBacktestAssetResult(Base):
    __tablename__ = "selected_universe_backtest_asset_results"
    __table_args__ = (
        UniqueConstraint("run_id", "asset_id", name="uq_selected_universe_backtest_asset"),
        CheckConstraint(
            "status IN ('eligible', 'insufficient_data')",
            name="ck_selected_universe_backtest_asset_status",
        ),
        Index("ix_selected_universe_backtest_asset_status", "run_id", "status"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("selected_universe_backtest_runs.id"), nullable=False
    )
    asset_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("assets.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_codes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    signal_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    transaction_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    realized_pnl: Mapped[float | None] = mapped_column(Numeric(20, 4))
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PriceCollectionTarget(Base):
    __tablename__ = "price_collection_targets"
    __table_args__ = (UniqueConstraint("source", "session_date", name="uq_price_collection_target"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class PriceCollectionItem(Base):
    __tablename__ = "price_collection_items"
    __table_args__ = (
        UniqueConstraint(
            "source", "asset_id", "session_date", name="uq_price_collection_item"
        ),
        Index("ix_price_collection_items_lookup", "source", "session_date", "status"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("assets.id"), nullable=False)
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class VirtualAccount(Base):
    __tablename__ = "virtual_accounts"
    __table_args__ = (
        UniqueConstraint("account_name", name="uq_virtual_accounts_account_name"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    account_name: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    initial_cash: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    state_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class VirtualAccountDailyState(Base):
    __tablename__ = "virtual_account_daily_states"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "decision_track",
            "session_date",
            name="uq_virtual_account_daily_state_track_session",
        ),
        Index(
            "ix_virtual_account_daily_states_lookup",
            "account_id",
            "decision_track",
            "session_date",
        ),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("virtual_accounts.id"), nullable=False
    )
    decision_track: Mapped[str] = mapped_column(String(32), nullable=False)
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_market_session: Mapped[date | None] = mapped_column(Date)
    price_latest_session: Mapped[date | None] = mapped_column(Date)
    data_delay_days: Mapped[int | None] = mapped_column(Integer)
    data_sources: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    quality_gate_status: Mapped[str] = mapped_column(String(32), nullable=False)
    quality_gate_reasons: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    observation_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_data_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    cash: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    equity: Mapped[float | None] = mapped_column(Numeric(20, 4))
    realized_pnl: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    unrealized_pnl: Mapped[float | None] = mapped_column(Numeric(20, 4))
    cumulative_pnl: Mapped[float | None] = mapped_column(Numeric(20, 4))
    maximum_drawdown: Mapped[float | None] = mapped_column(Numeric(12, 8))
    risk_halted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    positions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    pending_orders: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    signal_history: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class VirtualAccountEvent(Base):
    __tablename__ = "virtual_account_events"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "event_id", name="uq_virtual_account_event_id"
        ),
        Index(
            "ix_virtual_account_events_lookup",
            "account_id",
            "decision_track",
            "session_date",
            "event_type",
        ),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uuid_pk)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("virtual_accounts.id"), nullable=False
    )
    daily_state_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("virtual_account_daily_states.id"), nullable=False
    )
    decision_track: Mapped[str] = mapped_column(String(32), nullable=False)
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    input_data_version: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
