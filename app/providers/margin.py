"""Provider-neutral margin trading records.

No concrete paid provider is connected in MT-P0. These immutable records are
the contract that future J-Quants or broker-public-data adapters must satisfy.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator


MARGIN_PROVIDER_SCHEMA_VERSION = "margin-provider-schema-v1"


class MarginMarket(StrEnum):
    JP = "jp"
    US = "us"


class MarginAssetType(StrEnum):
    STOCK = "stock"
    ETF = "etf"


class CreditType(StrEnum):
    STANDARDIZED = "standardized"
    GENERAL = "general"
    DAY_TRADE = "day_trade"
    MARKET_SPECIFIC = "market_specific"
    NOT_APPLICABLE = "not_applicable"


class ShortAvailability(StrEnum):
    AVAILABLE = "available"
    LIMITED = "limited"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class MarginDataQuality(StrEnum):
    VERIFIED = "verified"
    PARTIAL = "partial"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    SYNTHETIC_RESEARCH = "synthetic_research"


class MarginSnapshotQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str = Field(min_length=1, max_length=64)
    symbol: str = Field(pattern=r"^[A-Z0-9.\-]{1,32}$")
    market: MarginMarket
    exchange: str = Field(min_length=1, max_length=64)
    asset_type: MarginAssetType
    broker_scope: str = Field(min_length=1, max_length=64)
    as_of: datetime

    @model_validator(mode="after")
    def validate_query_boundary(self):
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        if self.market == MarginMarket.JP and self.exchange.upper() != "JPX":
            raise ValueError("Japanese margin queries must use JPX")
        if self.market == MarginMarket.US and self.exchange.upper() == "JPX":
            raise ValueError("US margin queries cannot use JPX")
        return self


class MarginTradingSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_record_id: str = Field(min_length=1, max_length=128)
    asset_id: str = Field(min_length=1, max_length=64)
    symbol: str = Field(pattern=r"^[A-Z0-9.\-]{1,32}$")
    market: MarginMarket
    exchange: str = Field(min_length=1, max_length=64)
    asset_type: MarginAssetType
    broker_scope: str = Field(min_length=1, max_length=64)
    source: str = Field(min_length=1, max_length=64)
    source_version: str = Field(min_length=1, max_length=64)
    schema_version: str = MARGIN_PROVIDER_SCHEMA_VERSION
    currency: str = Field(pattern=r"^[A-Z]{3}$")

    margin_long_eligible: bool | None = None
    margin_short_eligible: bool | None = None
    credit_types: tuple[CreditType, ...] = ()
    is_lending_issue: bool | None = None
    short_availability: ShortAvailability = ShortAvailability.UNKNOWN
    restriction_codes: tuple[str, ...] = ()

    margin_interest_rate: float | None = Field(default=None, ge=0)
    stock_lending_fee: float | None = Field(default=None, ge=0)
    borrow_cost: float | None = Field(default=None, ge=0)
    reverse_stock_borrow_fee: float | None = Field(default=None, ge=0)
    initial_margin_rate: float | None = Field(default=None, ge=0, le=1)
    maintenance_margin_rate: float | None = Field(default=None, ge=0, le=1)
    minimum_margin_amount: float | None = Field(default=None, ge=0)
    repayment_term_days: int | None = Field(default=None, gt=0)
    forced_liquidation_rule_version: str | None = Field(default=None, max_length=64)

    effective_from: datetime
    effective_to: datetime | None = None
    available_at: datetime
    fetched_at: datetime
    data_quality: MarginDataQuality

    @model_validator(mode="after")
    def validate_market_and_time_boundaries(self):
        timestamps = [self.effective_from, self.available_at, self.fetched_at]
        if self.effective_to is not None:
            timestamps.append(self.effective_to)
        if any(value.tzinfo is None or value.utcoffset() is None for value in timestamps):
            raise ValueError("margin timestamps must be timezone-aware")
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be after effective_from")
        if self.fetched_at < self.available_at:
            raise ValueError("fetched_at cannot be before available_at")
        if len(self.credit_types) != len(set(self.credit_types)):
            raise ValueError("credit_types must not contain duplicates")

        japanese_types = {
            CreditType.STANDARDIZED,
            CreditType.GENERAL,
            CreditType.DAY_TRADE,
        }
        if self.market == MarginMarket.US:
            if japanese_types.intersection(self.credit_types):
                raise ValueError("Japanese credit types cannot be applied to US assets")
            if self.is_lending_issue is not None:
                raise ValueError("is_lending_issue is not applicable to US assets")
            if self.currency != "USD":
                raise ValueError("US margin snapshots must use USD")
        if self.market == MarginMarket.JP and CreditType.NOT_APPLICABLE in self.credit_types:
            raise ValueError("not_applicable credit type cannot be used for Japanese assets")
        if self.market == MarginMarket.JP:
            if self.exchange.upper() != "JPX":
                raise ValueError("Japanese margin snapshots must use JPX")
            if self.currency != "JPY":
                raise ValueError("Japanese margin snapshots must use JPY")
        if (
            CreditType.NOT_APPLICABLE in self.credit_types
            and len(self.credit_types) != 1
        ):
            raise ValueError("not_applicable credit type must be used alone")
        return self


@runtime_checkable
class MarginTradingProvider(Protocol):
    """Public or approved provider boundary; it never places broker orders."""

    name: str
    broker_scope: str

    def fetch_margin_snapshots(
        self,
        queries: tuple[MarginSnapshotQuery, ...],
    ) -> tuple[MarginTradingSnapshot, ...]: ...

    def health_check(self) -> dict[str, str | bool]: ...
