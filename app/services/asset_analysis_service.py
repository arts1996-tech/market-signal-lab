"""Versioned all-universe phase-3 analysis and paged read models."""

from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.analysis.movement_candidates import build_movement_candidates
from app.analysis.screening import SCREENING_MIN_HISTORY, screen_assets
from app.analysis.technical import (
    STOCHASTIC_RULE_VERSION,
    SUPPORT_RESISTANCE_RULE_VERSION,
)
from app.backtest.audit import frame_hash, json_value, stable_payload_hash
from app.core.config import get_settings
from app.core.data_source_policy import SOURCE_POLICY_VERSION
from app.database.models import (
    Asset,
    AssetAnalysisResult,
    AssetAnalysisRun,
)
from app.database.repositories import (
    list_assets_by_source,
    list_assets_with_minimum_price_history,
    market_prices_frame,
)


ASSET_ANALYSIS_NAME = "phase3_asset_screening"
ASSET_ANALYSIS_RULE_VERSION = "phase3-all-assets-v4"
ASSET_ANALYSIS_PAGE_SIZE_MAX = 200
INDEX_SYMBOLS = ["NASDAQCOM", "DJIA", "SP500", "NIKKEI225"]


def _source_policy() -> str:
    return "demo_only" if get_settings().market_data_mode == "demo" else "real_only"


def build_all_asset_analysis(
    assets: pd.DataFrame,
    prices: pd.DataFrame,
    index_prices: pd.DataFrame,
) -> dict:
    """Score every asset that passes the contiguous-session quality gate."""
    screening = screen_assets(
        prices,
        assets,
        min_history=SCREENING_MIN_HISTORY,
        benchmark_prices=index_prices,
        benchmark_symbol="NIKKEI225",
    )
    if screening.empty:
        return {
            "results": screening,
            "eligible_asset_count": 0,
            "movement_eligible_count": 0,
            "insufficient": pd.DataFrame(),
        }

    screening = screening.reset_index(drop=True)
    screening["attention_rank"] = range(1, len(screening) + 1)
    movement = build_movement_candidates(
        index_prices,
        prices,
        min_observations=SCREENING_MIN_HISTORY,
        limit=max(1, len(assets)),
    )
    movement_rows = movement["candidates"].copy()
    if movement_rows.empty:
        screening["movement_score"] = None
        screening["movement_rank"] = None
        screening["movement_direction"] = None
        screening["movement_reasons"] = [[] for _ in range(len(screening))]
    else:
        movement_rows = movement_rows.reset_index(drop=True)
        movement_rows["movement_rank"] = range(1, len(movement_rows) + 1)
        movement_rows = movement_rows.rename(
            columns={
                "score": "movement_score",
                "direction": "movement_direction",
                "reasons": "movement_reasons",
            }
        )
        screening = screening.merge(
            movement_rows[
                [
                    "symbol",
                    "movement_score",
                    "movement_rank",
                    "movement_direction",
                    "movement_reasons",
                ]
            ],
            on="symbol",
            how="left",
            validate="one_to_one",
        )

    asset_ids = assets.set_index("symbol")["asset_id"].to_dict()
    screening["asset_id"] = screening["symbol"].map(asset_ids)
    if screening["asset_id"].isna().any():
        raise ValueError("every analyzed symbol must map to one persisted asset")
    return {
        "results": screening,
        "eligible_asset_count": len(screening),
        "movement_eligible_count": int(movement.get("eligible_count", 0)),
        "insufficient": movement.get("insufficient", pd.DataFrame()),
    }


def _input_data_version(
    assets: pd.DataFrame,
    prices: pd.DataFrame,
    index_prices: pd.DataFrame,
) -> str:
    price_columns = [
        "symbol",
        "price_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "source",
        "source_symbol",
        "available_at",
        "price_basis",
        "data_quality_status",
    ]
    return stable_payload_hash(
        {
            "asset_universe": (
                sorted(assets["symbol"].astype(str).tolist())
                if "symbol" in assets
                else []
            ),
            "asset_prices": frame_hash(prices, price_columns),
            "index_prices": frame_hash(index_prices, price_columns),
            "rule_version": ASSET_ANALYSIS_RULE_VERSION,
            "source_policy_version": SOURCE_POLICY_VERSION,
        }
    )


def run_all_asset_analysis(session: Session, *, started_at: datetime | None = None) -> dict:
    """Analyze and persist the complete quality-gated universe without a row limit."""
    started = started_at or datetime.now(UTC)
    source_policy = _source_policy()
    asset_source = "sample" if source_policy == "demo_only" else "jquants"
    if source_policy == "demo_only":
        assets = list_assets_by_source(
            session, asset_source, asset_types=["stock", "etf"], limit=None
        )
    else:
        assets = list_assets_with_minimum_price_history(
            session,
            asset_source,
            asset_types=["stock", "etf"],
            min_history=SCREENING_MIN_HISTORY,
            limit=None,
            price_bases=["raw_ohlcv_with_adjusted"],
        )

    asset_rows = pd.DataFrame(
        [
            {
                "asset_id": asset.id,
                "symbol": asset.symbol,
                "name": asset.name,
                "asset_type": asset.asset_type,
                "metadata_json": asset.metadata_json or {},
            }
            for asset in assets
        ]
    )
    symbols = [asset.symbol for asset in assets]
    prices = (
        market_prices_frame(session, symbols, source_policy=source_policy)
        if symbols
        else pd.DataFrame()
    )
    if not prices.empty:
        prices = (
            prices.sort_values(["symbol", "price_time"])
            .groupby("symbol", group_keys=False)
            .tail(400)
            .reset_index(drop=True)
        )
    index_prices = market_prices_frame(
        session, INDEX_SYMBOLS, source_policy=source_policy
    )
    if not index_prices.empty:
        index_prices = (
            index_prices.sort_values(["symbol", "price_time"])
            .groupby("symbol", group_keys=False)
            .tail(400)
            .reset_index(drop=True)
        )

    input_version = _input_data_version(asset_rows, prices, index_prices)
    existing = session.scalar(
        select(AssetAnalysisRun).where(
            AssetAnalysisRun.analysis_name == ASSET_ANALYSIS_NAME,
            AssetAnalysisRun.data_scope == source_policy,
            AssetAnalysisRun.rule_version == ASSET_ANALYSIS_RULE_VERSION,
            AssetAnalysisRun.input_data_version == input_version,
        )
    )
    if existing is not None:
        return _run_summary(existing, reused=True)

    analysis = build_all_asset_analysis(asset_rows, prices, index_prices)
    results = analysis["results"]
    completed = datetime.now(UTC)
    data_as_of = None
    if not results.empty:
        data_as_of = pd.to_datetime(results["data_as_of"], utc=True).max().to_pydatetime()
    status = "success" if not results.empty else "insufficient_data"
    run = AssetAnalysisRun(
        analysis_name=ASSET_ANALYSIS_NAME,
        data_scope=source_policy,
        rule_version=ASSET_ANALYSIS_RULE_VERSION,
        source_policy_version=SOURCE_POLICY_VERSION,
        input_data_version=input_version,
        data_as_of=data_as_of,
        status=status,
        assets_considered=len(assets),
        eligible_asset_count=analysis["eligible_asset_count"],
        result_count=len(results),
        started_at=started,
        completed_at=completed,
        details={
            "universe_limit": None,
            "quality_gate_minimum_contiguous_sessions": SCREENING_MIN_HISTORY,
            "calendar_return_policy": "completed_xtks_period_close_to_close_v1",
            "atr_policy": "adjusted_ohlc_atr14_v1",
            "stochastic_policy": STOCHASTIC_RULE_VERSION,
            "support_resistance_policy": SUPPORT_RESISTANCE_RULE_VERSION,
            "relative_strength_policy": "exact_20_session_return_difference_v1",
            "sector_peer_minimum_total_assets": 3,
            "distance_52week_policy": "latest_close_vs_adjusted_high_252_contiguous_v1",
            "score_policy_unchanged": True,
            "movement_eligible_count": analysis["movement_eligible_count"],
            "quality_gate_excluded_count": len(assets) - len(results),
            "movement_insufficient_count": len(analysis["insufficient"]),
            "source_policy": source_policy,
        },
    )
    session.add(run)
    session.flush()
    for item in results.to_dict(orient="records"):
        payload = json_value(item)
        session.add(
            AssetAnalysisResult(
                run_id=run.id,
                asset_id=item["asset_id"],
                asset_type=str(item["asset_type"]),
                sector=str(item["sector"]),
                data_as_of=pd.Timestamp(item["data_as_of"]).to_pydatetime(),
                observations=int(item["observations"]),
                attention_score=int(item["attention_score"]),
                movement_score=(
                    None
                    if pd.isna(item.get("movement_score"))
                    else int(item["movement_score"])
                ),
                attention_rank=int(item["attention_rank"]),
                movement_rank=(
                    None
                    if pd.isna(item.get("movement_rank"))
                    else int(item["movement_rank"])
                ),
                result=payload,
            )
        )
    session.commit()
    return _run_summary(run, reused=False)


def _run_summary(run: AssetAnalysisRun, *, reused: bool) -> dict:
    return {
        "run_id": run.id,
        "status": run.status,
        "analysis_name": run.analysis_name,
        "data_scope": run.data_scope,
        "rule_version": run.rule_version,
        "source_policy_version": run.source_policy_version,
        "input_data_version": run.input_data_version,
        "data_as_of": run.data_as_of,
        "assets_considered": run.assets_considered,
        "eligible_asset_count": run.eligible_asset_count,
        "result_count": run.result_count,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "details": run.details or {},
        "reused": reused,
    }


def latest_asset_analysis_run(session: Session) -> AssetAnalysisRun | None:
    return session.scalar(
        select(AssetAnalysisRun)
        .where(
            AssetAnalysisRun.analysis_name == ASSET_ANALYSIS_NAME,
            AssetAnalysisRun.data_scope == _source_policy(),
        )
        .order_by(AssetAnalysisRun.completed_at.desc(), AssetAnalysisRun.id.desc())
        .limit(1)
    )


def asset_analysis_filter_options(session: Session) -> dict:
    run = latest_asset_analysis_run(session)
    if run is None:
        return {"run": None, "asset_types": [], "sectors": []}
    asset_types = list(
        session.scalars(
            select(AssetAnalysisResult.asset_type)
            .where(AssetAnalysisResult.run_id == run.id)
            .distinct()
            .order_by(AssetAnalysisResult.asset_type)
        )
    )
    sectors = list(
        session.scalars(
            select(AssetAnalysisResult.sector)
            .where(AssetAnalysisResult.run_id == run.id)
            .distinct()
            .order_by(AssetAnalysisResult.sector)
        )
    )
    return {
        "run": _run_summary(run, reused=True),
        "asset_types": asset_types,
        "sectors": sectors,
    }


def load_asset_analysis_page(
    session: Session,
    *,
    page: int = 1,
    page_size: int = 50,
    asset_types: list[str] | None = None,
    minimum_attention: int = 0,
    sectors: list[str] | None = None,
    symbol_query: str | None = None,
) -> dict:
    """Read one filtered UI page without changing the analyzed universe."""
    if page < 1:
        raise ValueError("page must be positive")
    if page_size < 1 or page_size > ASSET_ANALYSIS_PAGE_SIZE_MAX:
        raise ValueError(
            f"page_size must be between 1 and {ASSET_ANALYSIS_PAGE_SIZE_MAX}"
        )
    if not 0 <= minimum_attention <= 100:
        raise ValueError("minimum_attention must be between 0 and 100")
    run = latest_asset_analysis_run(session)
    if run is None:
        return {
            "run": None,
            "total": 0,
            "page": page,
            "page_size": page_size,
            "results": pd.DataFrame(),
        }

    filters = [
        AssetAnalysisResult.run_id == run.id,
        AssetAnalysisResult.attention_score >= minimum_attention,
    ]
    if asset_types:
        filters.append(AssetAnalysisResult.asset_type.in_(asset_types))
    if sectors:
        filters.append(AssetAnalysisResult.sector.in_(sectors))
    normalized_query = (symbol_query or "").strip()
    if normalized_query:
        filters.append(
            or_(
                Asset.symbol.ilike(f"%{normalized_query}%"),
                Asset.name.ilike(f"%{normalized_query}%"),
            )
        )

    total = int(
        session.scalar(
            select(func.count())
            .select_from(AssetAnalysisResult)
            .join(Asset, Asset.id == AssetAnalysisResult.asset_id)
            .where(*filters)
        )
        or 0
    )
    rows = session.execute(
        select(AssetAnalysisResult, Asset.symbol, Asset.name)
        .join(Asset, Asset.id == AssetAnalysisResult.asset_id)
        .where(*filters)
        .order_by(
            AssetAnalysisResult.attention_rank,
            Asset.symbol,
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    records = []
    for result, symbol, name in rows:
        payload = dict(result.result or {})
        payload.update(
            {
                "symbol": symbol,
                "name": name,
                "asset_type": result.asset_type,
                "sector": result.sector,
                "attention_score": result.attention_score,
                "attention_rank": result.attention_rank,
                "movement_score": result.movement_score,
                "movement_rank": result.movement_rank,
                "data_as_of": result.data_as_of,
                "observations": result.observations,
            }
        )
        records.append(payload)
    return {
        "run": _run_summary(run, reused=True),
        "total": total,
        "page": page,
        "page_size": page_size,
        "results": pd.DataFrame(records),
    }
