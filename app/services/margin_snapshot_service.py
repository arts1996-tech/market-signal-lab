"""Append and restore point-in-time margin data without guessing missing values."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.backtest.audit import stable_payload_hash
from app.database.models import (
    Asset,
    AssetTradingCapability,
    FinancingTermSnapshot,
    MarginMarketSnapshot,
    uuid_pk,
)
from app.providers.margin import MarginTradingSnapshot


class MarginSnapshotConflict(RuntimeError):
    """A provider record id was reused with different immutable content."""


def _normalized_asset_type(value: str) -> str | None:
    if value in {"stock", "jp_stock", "us_stock"}:
        return "stock"
    if value in {"etf", "jp_etf", "us_etf"}:
        return "etf"
    return None


def _validate_asset_identity(asset: Asset | None, snapshot: MarginTradingSnapshot) -> Asset:
    if asset is None:
        raise ValueError("margin snapshot references an unregistered asset")
    if asset.symbol.upper() != snapshot.symbol.upper():
        raise ValueError("margin snapshot symbol does not match the registered asset")
    if (asset.exchange or "").upper() != snapshot.exchange.upper():
        raise ValueError("margin snapshot exchange does not match the registered asset")
    if asset.currency != snapshot.currency:
        raise ValueError("margin snapshot currency does not match the registered asset")
    if _normalized_asset_type(asset.asset_type) != snapshot.asset_type.value:
        raise ValueError("margin snapshot asset type does not match the registered asset")
    return asset


def _insert_immutable(
    session: Session,
    *,
    model,
    constraint_name: str,
    values: dict[str, Any],
):
    row_id = uuid_pk()
    inserted_id = session.scalar(
        pg_insert(model)
        .values(id=row_id, **values)
        .on_conflict_do_nothing(constraint=constraint_name)
        .returning(model.id)
    )
    lookup_id = inserted_id or session.scalar(
        select(model.id).where(
            model.source == values["source"],
            model.provider_record_id == values["provider_record_id"],
            model.fetched_at == values["fetched_at"],
        )
    )
    row = session.get(model, lookup_id)
    if row is None:
        raise RuntimeError(f"{model.__tablename__} row could not be created or loaded")
    if row.input_hash != values["input_hash"]:
        raise MarginSnapshotConflict(
            "margin provider record is immutable and was reused with different content"
        )
    return row, inserted_id is not None


def persist_margin_snapshot(
    session: Session,
    snapshot: MarginTradingSnapshot,
) -> dict:
    """Persist the three normalized records in the caller-owned transaction."""

    _validate_asset_identity(session.get(Asset, snapshot.asset_id), snapshot)
    payload = snapshot.model_dump(mode="json")
    input_hash = stable_payload_hash(payload)
    provenance = {
        "asset_id": snapshot.asset_id,
        "provider_record_id": snapshot.provider_record_id,
        "market": snapshot.market.value,
        "broker_scope": snapshot.broker_scope,
        "source": snapshot.source,
        "source_version": snapshot.source_version,
        "schema_version": snapshot.schema_version,
        "data_quality_status": snapshot.data_quality.value,
        "input_hash": input_hash,
    }
    capability, capability_created = _insert_immutable(
        session,
        model=AssetTradingCapability,
        constraint_name="uq_asset_trading_capability_provider_record",
        values={
            **provenance,
            "asset_type": snapshot.asset_type.value,
            "margin_long_eligible": snapshot.margin_long_eligible,
            "margin_short_eligible": snapshot.margin_short_eligible,
            "credit_types": [value.value for value in snapshot.credit_types],
            "is_lending_issue": snapshot.is_lending_issue,
            "short_availability": snapshot.short_availability.value,
            "restriction_codes": list(snapshot.restriction_codes),
            "repayment_term_days": snapshot.repayment_term_days,
            "effective_from": snapshot.effective_from,
            "effective_to": snapshot.effective_to,
            "available_at": snapshot.available_at,
            "fetched_at": snapshot.fetched_at,
        },
    )
    market, market_created = _insert_immutable(
        session,
        model=MarginMarketSnapshot,
        constraint_name="uq_margin_market_snapshot_provider_record",
        values={
            **provenance,
            "currency": snapshot.currency,
            "session_date": snapshot.session_date,
            "margin_long_balance": snapshot.margin_long_balance,
            "margin_short_balance": snapshot.margin_short_balance,
            "lending_ratio": snapshot.lending_ratio,
            "reverse_stock_borrow_fee": snapshot.reverse_stock_borrow_fee,
            "effective_at": snapshot.effective_from,
            "available_at": snapshot.available_at,
            "fetched_at": snapshot.fetched_at,
        },
    )
    financing, financing_created = _insert_immutable(
        session,
        model=FinancingTermSnapshot,
        constraint_name="uq_financing_term_snapshot_provider_record",
        values={
            **provenance,
            "currency": snapshot.currency,
            "margin_interest_rate": snapshot.margin_interest_rate,
            "stock_lending_fee": snapshot.stock_lending_fee,
            "borrow_cost": snapshot.borrow_cost,
            "initial_margin_rate": snapshot.initial_margin_rate,
            "maintenance_margin_rate": snapshot.maintenance_margin_rate,
            "minimum_margin_amount": snapshot.minimum_margin_amount,
            "repayment_term_days": snapshot.repayment_term_days,
            "forced_liquidation_rule_version": (
                snapshot.forced_liquidation_rule_version
            ),
            "effective_from": snapshot.effective_from,
            "effective_to": snapshot.effective_to,
            "available_at": snapshot.available_at,
            "fetched_at": snapshot.fetched_at,
        },
    )
    return {
        "input_hash": input_hash,
        "capability_id": capability.id,
        "market_snapshot_id": market.id,
        "financing_term_id": financing.id,
        "created": {
            "capability": capability_created,
            "market_snapshot": market_created,
            "financing_term": financing_created,
        },
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    return value.astimezone(timezone.utc)


def load_margin_snapshot_as_of(
    session: Session,
    *,
    asset_id: str,
    market: str,
    broker_scope: str,
    as_of: datetime,
    source: str,
) -> MarginTradingSnapshot | None:
    """Restore only a record that was effective, available and fetched at ``as_of``."""

    cutoff = _as_utc(as_of)
    if not source:
        raise ValueError("source is required for margin snapshot restoration")
    conditions = [
        AssetTradingCapability.asset_id == asset_id,
        AssetTradingCapability.market == market,
        AssetTradingCapability.broker_scope == broker_scope,
        AssetTradingCapability.effective_from <= cutoff,
        or_(
            AssetTradingCapability.effective_to.is_(None),
            AssetTradingCapability.effective_to > cutoff,
        ),
        AssetTradingCapability.available_at <= cutoff,
        AssetTradingCapability.fetched_at <= cutoff,
        AssetTradingCapability.source == source,
    ]
    capability = session.scalar(
        select(AssetTradingCapability)
        .where(and_(*conditions))
        .order_by(
            AssetTradingCapability.available_at.desc(),
            AssetTradingCapability.fetched_at.desc(),
        )
        .limit(1)
    )
    if capability is None:
        return None
    market_snapshot = session.scalar(
        select(MarginMarketSnapshot).where(
            MarginMarketSnapshot.source == capability.source,
            MarginMarketSnapshot.provider_record_id == capability.provider_record_id,
            MarginMarketSnapshot.fetched_at == capability.fetched_at,
        )
    )
    financing = session.scalar(
        select(FinancingTermSnapshot).where(
            FinancingTermSnapshot.source == capability.source,
            FinancingTermSnapshot.provider_record_id == capability.provider_record_id,
            FinancingTermSnapshot.fetched_at == capability.fetched_at,
        )
    )
    asset = session.get(Asset, asset_id)
    if market_snapshot is None or financing is None or asset is None:
        raise RuntimeError("normalized margin snapshot is incomplete")
    return MarginTradingSnapshot(
        provider_record_id=capability.provider_record_id,
        asset_id=capability.asset_id,
        symbol=asset.symbol,
        market=capability.market,
        exchange=asset.exchange or "",
        asset_type=capability.asset_type,
        broker_scope=capability.broker_scope,
        source=capability.source,
        source_version=capability.source_version,
        schema_version=capability.schema_version,
        currency=market_snapshot.currency,
        margin_long_eligible=capability.margin_long_eligible,
        margin_short_eligible=capability.margin_short_eligible,
        credit_types=tuple(capability.credit_types),
        is_lending_issue=capability.is_lending_issue,
        short_availability=capability.short_availability,
        restriction_codes=tuple(capability.restriction_codes),
        session_date=market_snapshot.session_date,
        margin_long_balance=market_snapshot.margin_long_balance,
        margin_short_balance=market_snapshot.margin_short_balance,
        lending_ratio=market_snapshot.lending_ratio,
        margin_interest_rate=financing.margin_interest_rate,
        stock_lending_fee=financing.stock_lending_fee,
        borrow_cost=financing.borrow_cost,
        reverse_stock_borrow_fee=market_snapshot.reverse_stock_borrow_fee,
        initial_margin_rate=financing.initial_margin_rate,
        maintenance_margin_rate=financing.maintenance_margin_rate,
        minimum_margin_amount=financing.minimum_margin_amount,
        repayment_term_days=financing.repayment_term_days,
        forced_liquidation_rule_version=financing.forced_liquidation_rule_version,
        effective_from=capability.effective_from,
        effective_to=capability.effective_to,
        available_at=capability.available_at,
        fetched_at=capability.fetched_at,
        data_quality=capability.data_quality_status,
    )
