from collections.abc import Iterable
from datetime import datetime

import pandas as pd
from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.database.models import ApiFetchLog, Asset, CorrelationResult, JobRun, MarketPrice


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
            "volume": stmt.excluded.volume,
            "fetched_at": stmt.excluded.fetched_at,
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
) -> pd.DataFrame:
    query: Select = (
        select(
            Asset.symbol,
            Asset.name,
            MarketPrice.price_time,
            MarketPrice.close,
            MarketPrice.source,
            MarketPrice.fetched_at,
        )
        .join(MarketPrice, MarketPrice.asset_id == Asset.id)
        .where(Asset.symbol.in_(symbols))
        .order_by(MarketPrice.price_time)
    )
    if start:
        query = query.where(MarketPrice.price_time >= start)
    if end:
        query = query.where(MarketPrice.price_time <= end)

    rows = session.execute(query).mappings().all()
    return pd.DataFrame(rows)


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


def upsert_correlation_results(session: Session, rows: Iterable[dict]) -> int:
    payload = list(rows)
    if not payload:
        return 0

    stmt = pg_insert(CorrelationResult).values(payload)
    session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_correlation_result",
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


def latest_correlation_results(session: Session, limit: int = 50) -> list[CorrelationResult]:
    return list(
        session.scalars(
            select(CorrelationResult)
            .order_by(CorrelationResult.period_end.desc(), CorrelationResult.window_days.asc())
            .limit(limit)
        )
    )


def insert_job_run(session: Session, **values) -> None:
    session.add(JobRun(**values))


def latest_job_runs(session: Session, limit: int = 20) -> list[JobRun]:
    return list(session.scalars(select(JobRun).order_by(JobRun.started_at.desc()).limit(limit)))
