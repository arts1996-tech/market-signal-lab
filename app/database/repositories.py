from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta

import pandas as pd
from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, aliased

from app.database.models import (
    ApiFetchLog,
    Asset,
    CorrelationResult,
    EtfMetricSnapshot,
    FundamentalSnapshot,
    JobRun,
    MarketPrice,
    PriceCollectionItem,
    PriceCollectionTarget,
    SpilloverFeature,
    SpilloverModelResult,
    VirtualAccount,
    VirtualAccountDailyState,
    VirtualAccountEvent,
    uuid_pk,
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


def insert_fundamental_snapshots(session: Session, rows: Iterable[dict]) -> int:
    """Append new provider snapshots and ignore an exact source/timing replay."""
    payload = list(rows)
    if not payload:
        return 0
    statement = (
        pg_insert(FundamentalSnapshot)
        .values(payload)
        .on_conflict_do_nothing(constraint="uq_fundamental_snapshot")
        .returning(FundamentalSnapshot.id)
    )
    return len(session.scalars(statement).all())


def insert_etf_metric_snapshots(session: Session, rows: Iterable[dict]) -> int:
    """Append reviewed ETF metrics and ignore an exact source/timing replay."""
    payload = list(rows)
    if not payload:
        return 0
    statement = (
        pg_insert(EtfMetricSnapshot)
        .values(payload)
        .on_conflict_do_nothing(constraint="uq_etf_metric_snapshot")
        .returning(EtfMetricSnapshot.id)
    )
    return len(session.scalars(statement).all())


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


def list_assets_with_minimum_price_history(
    session: Session,
    source: str,
    asset_types: list[str],
    min_history: int,
    limit: int | None,
    price_bases: list[str] | None = None,
) -> list[Asset]:
    """Select assets that already satisfy a distinct-session history gate."""
    query = (
        select(Asset)
        .join(MarketPrice, MarketPrice.asset_id == Asset.id)
        .where(
            Asset.source == source,
            Asset.asset_type.in_(asset_types),
            MarketPrice.source == source,
            MarketPrice.timeframe == "1d",
            MarketPrice.adjusted_close.is_not(None),
        )
        .group_by(Asset.id)
        .having(func.count(func.distinct(MarketPrice.session_date)) >= min_history)
        .order_by(
            func.count(func.distinct(MarketPrice.session_date)).desc(),
            func.max(MarketPrice.session_date).desc(),
            Asset.symbol,
        )
    )
    if price_bases:
        query = query.where(MarketPrice.price_basis.in_(price_bases))
    if limit is not None:
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
    retry_item = aliased(PriceCollectionItem)
    price = MarketPrice
    retry_cooldown = datetime.now(UTC) - timedelta(minutes=5)
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
        .outerjoin(
            retry_item,
            and_(
                retry_item.asset_id == Asset.id,
                retry_item.source == source,
                retry_item.session_date == session_date,
            ),
        )
        .where(
            Asset.source == source,
            Asset.asset_type.in_(asset_types),
            price.id.is_(None),
            terminal_item.id.is_(None),
            or_(
                retry_item.id.is_(None),
                retry_item.status != "retry_pending",
                retry_item.attempted_at < retry_cooldown,
            ),
        )
        .order_by(
            case((retry_item.status == "retry_pending", 0), else_=1),
            retry_item.attempted_at,
            Asset.symbol,
        )
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


def collection_target_records(
    session: Session, source: str, session_dates: list[date]
) -> dict[date, PriceCollectionTarget]:
    """Return collection targets with timestamps for scheduling decisions."""
    if not session_dates:
        return {}
    rows = session.scalars(
        select(PriceCollectionTarget).where(
            PriceCollectionTarget.source == source,
            PriceCollectionTarget.session_date.in_(session_dates),
        )
    )
    return {row.session_date: row for row in rows}


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


def latest_job_runs(
    session: Session,
    limit: int = 20,
    job_name: str | None = None,
) -> list[JobRun]:
    query = select(JobRun).order_by(JobRun.started_at.desc())
    if job_name:
        query = query.where(JobRun.job_name == job_name)
    return list(session.scalars(query.limit(limit)))


def latest_successful_job_run(session: Session, job_name: str) -> JobRun | None:
    return session.scalar(
        select(JobRun)
        .where(JobRun.job_name == job_name, JobRun.status == "success")
        .order_by(JobRun.finished_at.desc())
        .limit(1)
    )


class VirtualAccountStateConflict(RuntimeError):
    """Raised when a frozen account/day would be replaced or become non-deterministic."""


def get_or_create_virtual_account(session: Session, values: dict) -> VirtualAccount:
    account_id = uuid_pk()
    stmt = (
        pg_insert(VirtualAccount)
        .values(id=account_id, **values)
        .on_conflict_do_nothing(constraint="uq_virtual_accounts_account_name")
        .returning(VirtualAccount.id)
    )
    inserted_id = session.scalar(stmt)
    lookup = (
        VirtualAccount.id == inserted_id
        if inserted_id
        else VirtualAccount.account_name == values["account_name"]
    )
    account = session.scalar(
        select(VirtualAccount).where(lookup)
    )
    if account is None:
        raise RuntimeError("virtual account could not be created or loaded")
    immutable_fields = ("label", "currency", "strategy_version", "state_version")
    mismatched = [
        field
        for field in immutable_fields
        if str(getattr(account, field)) != str(values[field])
    ]
    if float(account.initial_cash) != float(values["initial_cash"]):
        mismatched.append("initial_cash")
    if mismatched:
        raise VirtualAccountStateConflict(
            f"virtual account metadata is immutable; mismatched fields: {mismatched}"
        )
    return account


def insert_virtual_account_daily_state(
    session: Session,
    values: dict,
) -> tuple[VirtualAccountDailyState, bool]:
    state_id = uuid_pk()
    stmt = (
        pg_insert(VirtualAccountDailyState)
        .values(id=state_id, **values)
        .on_conflict_do_nothing(
            constraint="uq_virtual_account_daily_state_track_session"
        )
        .returning(VirtualAccountDailyState.id)
    )
    inserted_id = session.scalar(stmt)
    if inserted_id:
        state = session.get(VirtualAccountDailyState, inserted_id)
        if state is None:
            raise RuntimeError("inserted virtual account state could not be loaded")
        return state, True

    state = session.scalar(
        select(VirtualAccountDailyState).where(
            VirtualAccountDailyState.account_id == values["account_id"],
            VirtualAccountDailyState.decision_track == values["decision_track"],
            VirtualAccountDailyState.session_date == values["session_date"],
        )
    )
    if state is None:
        raise RuntimeError("existing virtual account state could not be loaded")
    if (
        state.input_data_version == values["input_data_version"]
        and state.input_hash == values["input_hash"]
    ):
        return state, False
    raise VirtualAccountStateConflict(
        "virtual account day is already frozen with a different input or result"
    )


def insert_virtual_account_events(session: Session, rows: Iterable[dict]) -> int:
    payload = list(rows)
    if not payload:
        return 0
    stmt = pg_insert(VirtualAccountEvent).values(payload)
    inserted_ids = session.scalars(
        stmt.on_conflict_do_nothing(
            constraint="uq_virtual_account_event_id"
        ).returning(VirtualAccountEvent.id)
    )
    return len(list(inserted_ids))


def latest_virtual_account_daily_state(
    session: Session,
    account_id: str,
    decision_track: str,
) -> VirtualAccountDailyState | None:
    return session.scalar(
        select(VirtualAccountDailyState)
        .where(
            VirtualAccountDailyState.account_id == account_id,
            VirtualAccountDailyState.decision_track == decision_track,
        )
        .order_by(VirtualAccountDailyState.session_date.desc())
        .limit(1)
    )


def list_virtual_accounts(session: Session) -> list[VirtualAccount]:
    return list(session.scalars(select(VirtualAccount).order_by(VirtualAccount.account_name)))


def get_virtual_account_by_name(
    session: Session,
    account_name: str,
) -> VirtualAccount | None:
    return session.scalar(
        select(VirtualAccount).where(VirtualAccount.account_name == account_name)
    )


def virtual_account_daily_state_for_date(
    session: Session,
    account_id: str,
    decision_track: str,
    session_date: date,
) -> VirtualAccountDailyState | None:
    return session.scalar(
        select(VirtualAccountDailyState).where(
            VirtualAccountDailyState.account_id == account_id,
            VirtualAccountDailyState.decision_track == decision_track,
            VirtualAccountDailyState.session_date == session_date,
        )
    )


def virtual_account_events_for_state(
    session: Session,
    daily_state_id: str,
) -> list[VirtualAccountEvent]:
    return list(
        session.scalars(
            select(VirtualAccountEvent)
            .where(VirtualAccountEvent.daily_state_id == daily_state_id)
            .order_by(VirtualAccountEvent.event_at, VirtualAccountEvent.event_id)
        )
    )
