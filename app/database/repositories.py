from collections.abc import Iterable
from datetime import date, datetime

import pandas as pd
from sqlalchemy import Select, and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.database.models import (
    ApiFetchLog,
    Asset,
    CorrelationResult,
    JobRun,
    MarketPrice,
    PriceCollectionItem,
    PriceCollectionTarget,
    SpilloverFeature,
    SpilloverModelResult,
)
from app.core.data_source_policy import source_priority


ASSET_DEFINITIONS = [
    {
        "symbol": "NASDAQCOM",
        "name": "NASDAQ Composite",
        "asset_type": "index",
        "currency": "USD",
        "exchange": "NASDAQ",
        "source": "fred",
    },
    {
        "symbol": "DJIA",
        "name": "Dow Jones Industrial Average",
        "asset_type": "index",
        "currency": "USD",
        "exchange": "NYSE/NASDAQ",
        "source": "fred",
    },
    {
        "symbol": "SP500",
        "name": "S&P 500",
        "asset_type": "index",
        "currency": "USD",
        "exchange": "NYSE/NASDAQ",
        "source": "fred",
    },
    {
        "symbol": "NIKKEI225",
        "name": "Nikkei 225",
        "asset_type": "index",
        "currency": "JPY",
        "exchange": "JPX",
        "source": "fred",
    },
    {
        "symbol": "DEXJPUS",
        "name": "USD/JPY",
        "asset_type": "fx",
        "currency": "JPY",
        "exchange": "FX",
        "source": "fred",
    },
]


def chunked(items: list[dict], size: int) -> Iterable[list[dict]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def upsert_assets(session: Session, assets: Iterable[dict]) -> dict[str, Asset]:
    for item in assets:
        stmt = (
            pg_insert(Asset)
            .values(**item)
            .on_conflict_do_update(
                index_elements=[Asset.symbol],
                set_={
                    "name": item["name"],
                    "asset_type": item["asset_type"],
                    "currency": item["currency"],
                    "exchange": item.get("exchange"),
                    "source": item.get("source", "manual"),
                },
            )
        )
        session.execute(stmt)
    session.flush()
    rows = session.scalars(select(Asset).where(Asset.symbol.in_([a["symbol"] for a in assets]))).all()
    return {row.symbol: row for row in rows}


def upsert_market_prices(session: Session, rows: Iterable[dict]) -> int:
    payload = list(rows)
    if not payload:
        return 0

    for batch in chunked(payload, 1_000):
        stmt = pg_insert(MarketPrice).values(batch)
        update_columns = {
            "open": stmt.excluded.open,
            "high": stmt.excluded.high,
            "low": stmt.excluded.low,
            "close": stmt.excluded.close,
            "adjusted_close": stmt.excluded.adjusted_close,
            "adjusted_open": stmt.excluded.adjusted_open,
            "adjusted_high": stmt.excluded.adjusted_high,
            "adjusted_low": stmt.excluded.adjusted_low,
            "adjusted_volume": stmt.excluded.adjusted_volume,
            "adjustment_factor": stmt.excluded.adjustment_factor,
            "volume": stmt.excluded.volume,
            "fetched_at": stmt.excluded.fetched_at,
            "available_at": stmt.excluded.available_at,
            "data_quality_status": stmt.excluded.data_quality_status,
            "price_basis": stmt.excluded.price_basis,
        }
        session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_market_price",
                set_=update_columns,
            )
        )
    return len(payload)


def market_prices_frame(
    session: Session,
    symbols: list[str],
    start: datetime | None = None,
    end: datetime | None = None,
    source_policy: str = "real_only",
) -> pd.DataFrame:
    if source_policy not in {"real_only", "demo_only"}:
        raise ValueError("source_policy must be 'real_only' or 'demo_only'")
    query: Select = (
        select(
            MarketPrice.id.label("price_id"),
            Asset.id.label("asset_id"),
            Asset.symbol,
            Asset.name,
            Asset.asset_type,
            Asset.currency,
            MarketPrice.timeframe,
            MarketPrice.price_time,
            MarketPrice.open,
            MarketPrice.high,
            MarketPrice.low,
            MarketPrice.close,
            MarketPrice.adjusted_close,
            MarketPrice.adjusted_open,
            MarketPrice.adjusted_high,
            MarketPrice.adjusted_low,
            MarketPrice.adjusted_volume,
            MarketPrice.adjustment_factor,
            MarketPrice.volume,
            MarketPrice.source,
            MarketPrice.source_symbol,
            MarketPrice.session_date,
            MarketPrice.fetched_at,
            MarketPrice.available_at,
            MarketPrice.data_quality_status,
            MarketPrice.price_basis,
        )
        .join(MarketPrice, MarketPrice.asset_id == Asset.id)
        .where(Asset.symbol.in_(symbols))
        .order_by(MarketPrice.price_time, MarketPrice.id)
    )
    if source_policy == "real_only":
        query = query.where(MarketPrice.source != "sample")
    else:
        query = query.where(MarketPrice.source == "sample")
    if start:
        query = query.where(MarketPrice.price_time >= start)
    if end:
        query = query.where(MarketPrice.price_time <= end)

    rows = session.execute(query).mappings().all()
    return resolve_market_price_sources(pd.DataFrame(rows), source_policy)


def resolve_market_price_sources(prices: pd.DataFrame, source_policy: str = "real_only") -> pd.DataFrame:
    """Apply the versioned source policy before data reaches any analysis code."""
    if prices.empty:
        return prices
    frame = prices.copy()
    if source_policy == "demo_only":
        selected = frame[frame["source"] == "sample"].copy()
    else:
        frame["source_priority"] = [
            source_priority(row.asset_type, row.currency, row.timeframe, row.source)
            for row in frame.itertuples(index=False)
        ]
        selected = frame[frame["source_priority"].notna()].copy()
        selected = selected[
            ~((selected["source"] == "jquants") & (selected["price_basis"] != "raw_ohlcv_with_adjusted"))
        ]
        selected["source_priority"] = selected["source_priority"].astype(int)
        selected = selected.sort_values(
            ["asset_id", "timeframe", "price_time", "source_priority", "available_at", "fetched_at", "price_id"],
            ascending=[True, True, True, True, False, False, False],
            na_position="last",
        ).drop_duplicates(["asset_id", "timeframe", "price_time"], keep="first")
    adjusted = selected["price_basis"].eq("raw_ohlcv_with_adjusted")
    for raw_column, adjusted_column in (("open", "adjusted_open"), ("high", "adjusted_high"), ("low", "adjusted_low"), ("close", "adjusted_close"), ("volume", "adjusted_volume")):
        if raw_column in selected and adjusted_column in selected:
            usable = adjusted & selected[adjusted_column].notna()
            selected[raw_column] = pd.to_numeric(selected[raw_column], errors="coerce").astype(float)
            adjusted_values = pd.to_numeric(selected[adjusted_column], errors="coerce").astype(float)
            selected.loc[usable, f"raw_{raw_column}"] = selected.loc[usable, raw_column]
            selected.loc[usable, raw_column] = adjusted_values.loc[usable]
    internal_columns = ["price_id", "asset_id", "asset_type", "currency", "timeframe", "source_priority"]
    return selected.drop(columns=[column for column in internal_columns if column in selected], errors="ignore")


def insert_api_fetch_log(session: Session, **values) -> None:
    session.add(ApiFetchLog(**values))


def latest_fetch_logs(session: Session, limit: int = 20) -> list[ApiFetchLog]:
    return list(session.scalars(select(ApiFetchLog).order_by(ApiFetchLog.fetched_at.desc()).limit(limit)))


def list_assets_by_source(
    session: Session,
    source: str,
    asset_types: list[str] | None = None,
    limit: int | None = None,
) -> list[Asset]:
    query = select(Asset).where(Asset.source == source).order_by(Asset.symbol)
    if asset_types:
        query = query.where(Asset.asset_type.in_(asset_types))
    if limit:
        query = query.limit(limit)
    return list(session.scalars(query))


def list_assets_missing_price_for_date(
    session: Session,
    source: str,
    session_date: date,
    asset_types: list[str],
    limit: int,
) -> list[Asset]:
    terminal_item = PriceCollectionItem
    price = MarketPrice
    query = (
        select(Asset)
        .outerjoin(
            price,
            and_(
                price.asset_id == Asset.id,
                price.source == source,
                price.session_date == session_date,
                price.timeframe == "1d",
            ),
        )
        .outerjoin(
            terminal_item,
            and_(
                terminal_item.asset_id == Asset.id,
                terminal_item.source == source,
                terminal_item.session_date == session_date,
                terminal_item.status == "no_data",
            ),
        )
        .where(
            Asset.source == source,
            Asset.asset_type.in_(asset_types),
            price.id.is_(None),
            terminal_item.id.is_(None),
        )
        .order_by(Asset.symbol)
        .limit(limit)
    )
    return list(session.scalars(query))


def collection_target_statuses(
    session: Session, source: str, session_dates: list[date]
) -> dict[date, str]:
    if not session_dates:
        return {}
    rows = session.scalars(
        select(PriceCollectionTarget).where(
            PriceCollectionTarget.source == source,
            PriceCollectionTarget.session_date.in_(session_dates),
        )
    )
    return {row.session_date: row.status for row in rows}


def has_collected_price_for_date(session: Session, source: str, session_date: date) -> bool:
    count = session.scalar(
        select(func.count())
        .select_from(MarketPrice)
        .where(
            MarketPrice.source == source,
            MarketPrice.session_date == session_date,
            MarketPrice.timeframe == "1d",
        )
    )
    return bool(count)


def upsert_collection_target(
    session: Session,
    source: str,
    session_date: date,
    status: str,
    checked_at: datetime,
    details: dict | None = None,
) -> None:
    stmt = pg_insert(PriceCollectionTarget).values(
        source=source,
        session_date=session_date,
        status=status,
        checked_at=checked_at,
        details=details or {},
    )
    session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_price_collection_target",
            set_={
                "status": stmt.excluded.status,
                "checked_at": stmt.excluded.checked_at,
                "details": stmt.excluded.details,
            },
        )
    )


def upsert_unavailable_collection_items(
    session: Session,
    source: str,
    session_date: date,
    assets_by_symbol: dict[str, Asset],
    details_by_symbol: dict[str, dict],
    attempted_at: datetime,
) -> int:
    payload = [
        {
            "source": source,
            "asset_id": assets_by_symbol[symbol].id,
            "session_date": session_date,
            "status": "no_data",
            "attempted_at": attempted_at,
            "details": {"reason": details_by_symbol[symbol].get("message", "No daily price returned")},
        }
        for symbol in details_by_symbol
        if details_by_symbol[symbol].get("status") == "no_data" and symbol in assets_by_symbol
    ]
    if not payload:
        return 0
    stmt = pg_insert(PriceCollectionItem).values(payload)
    session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_price_collection_item",
            set_={
                "status": stmt.excluded.status,
                "attempted_at": stmt.excluded.attempted_at,
                "details": stmt.excluded.details,
            },
        )
    )
    return len(payload)


def upsert_correlation_results(session: Session, rows: Iterable[dict]) -> int:
    payload = list(rows)
    if not payload:
        return 0

    stmt = pg_insert(CorrelationResult).values(payload)
    session.execute(
        stmt.on_conflict_do_update(
                constraint="uq_correlation_result_input",
            set_={
                "correlation": stmt.excluded.correlation,
                "sample_size": stmt.excluded.sample_size,
                "period_start": stmt.excluded.period_start,
                "computed_at": stmt.excluded.computed_at,
                "source": stmt.excluded.source,
                "details": stmt.excluded.details,
            },
        )
    )
    return len(payload)


def latest_correlation_results(
    session: Session, limit: int = 50, analysis_status: str | None = "current"
) -> list[CorrelationResult]:
    query = select(CorrelationResult).order_by(
        CorrelationResult.period_end.desc(), CorrelationResult.window_days.asc()
    )
    if analysis_status:
        query = query.where(CorrelationResult.analysis_status == analysis_status)
    return list(session.scalars(query.limit(limit)))


def upsert_spillover_features(session: Session, rows: Iterable[dict]) -> int:
    payload = list(rows)
    if not payload:
        return 0
    stmt = pg_insert(SpilloverFeature).values(payload)
    session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_spillover_feature_input",
            set_={
                "us_session_date": stmt.excluded.us_session_date,
                "us_return": stmt.excluded.us_return,
                "target_return": stmt.excluded.target_return,
                "lag_rule": stmt.excluded.lag_rule,
                "computed_at": stmt.excluded.computed_at,
                "details": stmt.excluded.details,
            },
        )
    )
    return len(payload)


def latest_spillover_features(
    session: Session, limit: int = 100, analysis_status: str | None = "current"
) -> list[SpilloverFeature]:
    query = select(SpilloverFeature).order_by(
        SpilloverFeature.japan_session_date.desc(), SpilloverFeature.metric
    )
    if analysis_status:
        query = query.where(SpilloverFeature.analysis_status == analysis_status)
    return list(session.scalars(query.limit(limit)))


def upsert_spillover_model_results(session: Session, rows: Iterable[dict]) -> int:
    payload = list(rows)
    if not payload:
        return 0
    stmt = pg_insert(SpilloverModelResult).values(payload)
    session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_spillover_model_result_input",
            set_={
                "sample_size": stmt.excluded.sample_size,
                "r_squared": stmt.excluded.r_squared,
                "computed_at": stmt.excluded.computed_at,
                "model_version": stmt.excluded.model_version,
                "details": stmt.excluded.details,
            },
        )
    )
    return len(payload)


def latest_spillover_model_results(
    session: Session, limit: int = 100, analysis_status: str | None = "current"
) -> list[SpilloverModelResult]:
    query = select(SpilloverModelResult).order_by(
        SpilloverModelResult.period_end.desc(), SpilloverModelResult.window_days
    )
    if analysis_status:
        query = query.where(SpilloverModelResult.analysis_status == analysis_status)
    return list(session.scalars(query.limit(limit)))


def insert_job_run(session: Session, **values) -> None:
    session.add(JobRun(**values))


def latest_job_runs(session: Session, limit: int = 20) -> list[JobRun]:
    return list(session.scalars(select(JobRun).order_by(JobRun.started_at.desc()).limit(limit)))


def latest_successful_job_run(session: Session, job_name: str) -> JobRun | None:
    return session.scalar(
        select(JobRun)
        .where(JobRun.job_name == job_name, JobRun.status == "success")
        .order_by(JobRun.finished_at.desc())
        .limit(1)
    )
