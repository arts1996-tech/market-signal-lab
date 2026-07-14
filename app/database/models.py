from datetime import date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
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
