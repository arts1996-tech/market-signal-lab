from datetime import UTC, datetime
from hashlib import sha256
import json

import pandas as pd
from sqlalchemy.orm import Session

from app.analysis.correlation import (
    WINDOWS,
    close_wide,
    conditional_next_day_stats,
    horizon_correlations,
    normalized_index,
    rolling_correlation,
    us_japan_pair_frame,
)
from app.analysis.movement_candidates import build_movement_candidates
from app.analysis.regression import rolling_ols, run_granger_test, run_ols, walk_forward_ols
from app.analysis.spillover import TARGET_METRICS, spillover_conditional_stats, us_japan_spillover_frame
from app.analysis.sensitivity import sector_sensitivity
from app.analysis.screening import screen_assets
from app.analysis.technical import short_term_indicator_frame, short_term_signal_snapshot
from app.analysis.virtual_trading import build_virtual_trades, summarize_virtual_trade_feedback
from app.database.repositories import (
    list_assets_by_source,
    market_prices_frame,
    upsert_correlation_results,
    upsert_spillover_features,
    upsert_spillover_model_results,
)
from app.core.config import Settings, get_settings
from app.core.data_source_policy import SOURCE_POLICY_VERSION


DEFAULT_SYMBOLS = ["NASDAQCOM", "DJIA", "SP500", "NIKKEI225", "DEXJPUS"]
US_INDEX_SYMBOLS = ["NASDAQCOM", "DJIA", "SP500"]
JAPAN_INDEX_SYMBOLS = ["NIKKEI225"]
US_JAPAN_LAG_RULE = "us_previous_trading_day_to_japan_current_day"
SPILLOVER_MODEL_VERSION = "ols_us_return_v1"
SPILLOVER_GRANGER_VERSION = "granger_us_return_v1"


def market_price_source_policy(settings: Settings | None = None) -> str:
    """Keep synthetic prices isolated from every normal analysis path."""
    settings = settings or get_settings()
    return "demo_only" if settings.market_data_mode == "demo" else "real_only"


def build_analysis_input_provenance(prices: pd.DataFrame) -> dict:
    """Fingerprint the already policy-selected rows used by an analysis run."""
    columns = [
        "symbol",
        "timeframe",
        "price_time",
        "source",
        "source_symbol",
        "open",
        "high",
        "low",
        "close",
        "adjusted_close",
        "adjusted_open",
        "adjusted_high",
        "adjusted_low",
        "adjustment_factor",
        "volume",
        "data_quality_status",
        "price_basis",
        "available_at",
    ]
    available_columns = [column for column in columns if column in prices]
    canonical = prices[available_columns].copy() if available_columns else pd.DataFrame()
    if not canonical.empty:
        for column in canonical.columns:
            canonical[column] = canonical[column].map(
                lambda value: None if pd.isna(value) else str(value)
            )
        canonical = canonical.sort_values(available_columns).reset_index(drop=True)
    payload = canonical.to_dict(orient="records")
    input_data_version = sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    sources = []
    if not prices.empty:
        for symbol, group in prices.groupby("symbol", sort=True):
            sources.append(
                {
                    "symbol": symbol,
                    "sources": sorted(group["source"].dropna().unique().tolist()),
                    "source_symbols": sorted(group["source_symbol"].dropna().unique().tolist()),
                    "observations": len(group),
                    "period_start": str(pd.to_datetime(group["price_time"], utc=True).min()),
                    "period_end": str(pd.to_datetime(group["price_time"], utc=True).max()),
                    "quality_status_counts": group["data_quality_status"].value_counts().to_dict(),
                    "price_bases": sorted(group["price_basis"].dropna().unique().tolist()),
                    "latest_available_at": str(pd.to_datetime(group["available_at"], utc=True).max()),
                }
            )
    return {
        "input_data_version": input_data_version,
        "source_policy_version": SOURCE_POLICY_VERSION,
        "input_provenance": {
            "source_policy": market_price_source_policy(),
            "price_basis_policy_version": "adjusted_preferred_v1",
            "price_basis": "adjusted_ohlcv_when_available_else_provider_reported",
            "session_integrity_policy_version": "weekday_strict_v1",
            "assets": sources,
        },
    }


def analysis_provenance_fields(provenance: dict | None) -> dict:
    provenance = provenance or {
        "input_data_version": "untracked-direct-call",
        "source_policy_version": SOURCE_POLICY_VERSION,
        "input_provenance": {"reason": "direct_analysis_call"},
    }
    return {
        "input_data_version": provenance["input_data_version"],
        "source_policy_version": provenance["source_policy_version"],
        "analysis_status": "current",
        "input_provenance": provenance["input_provenance"],
    }


def load_market_analysis(session: Session, symbols: list[str] | None = None) -> dict:
    prices = market_prices_frame(
        session, symbols or DEFAULT_SYMBOLS, source_policy=market_price_source_policy()
    )
    # The dashboard only needs the recent windows (up to 250 sessions). Keep
    # the full history in PostgreSQL, but avoid recalculating calendar alignment
    # over decades on every Streamlit rerun.
    if not prices.empty:
        prices = (
            prices.sort_values(["symbol", "price_time"])
            .groupby("symbol", group_keys=False)
            .tail(400)
            .sort_values("price_time")
            .reset_index(drop=True)
        )
    wide = close_wide(prices)
    pair = us_japan_pair_frame(wide, "NASDAQCOM", "NIKKEI225", calendar_aware=True)
    return {
        "prices": prices,
        "wide": wide,
        "normalized": normalized_index(wide),
        "pair": pair,
        "horizon_correlations": horizon_correlations(pair),
        "rolling_correlation": rolling_correlation(pair, 60),
        "conditional_stats": conditional_next_day_stats(pair),
        "data_quality_warnings": build_data_quality_warnings(prices),
        "input_provenance": build_analysis_input_provenance(prices),
    }


def build_data_quality_warnings(
    prices: pd.DataFrame,
    stale_after_days: int | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """Return explicit warnings for missing or stale daily observations."""
    if prices.empty:
        return [{"symbol": None, "status": "missing", "message": "価格データがありません。"}]

    from app.core.config import get_settings

    threshold = stale_after_days or get_settings().data_stale_after_days
    current = pd.Timestamp(now or datetime.now(UTC))
    if current.tzinfo is None:
        current = current.tz_localize("UTC")
    else:
        current = current.tz_convert("UTC")

    warnings = []
    frame = prices.copy()
    frame["price_time"] = pd.to_datetime(frame["price_time"], utc=True)
    for symbol, group in frame.groupby("symbol"):
        latest = group["price_time"].max()
        age_days = max(0, (current.normalize() - latest.normalize()).days)
        if age_days > threshold:
            warnings.append(
                {
                    "symbol": symbol,
                    "status": "stale",
                    "latest_price_time": latest.to_pydatetime(),
                    "age_days": age_days,
                    "message": f"{symbol} の最新価格は {age_days} 日前です。",
                }
            )
    return warnings


def build_analysis_status(prices: pd.DataFrame, warnings: list[dict], settings: Settings | None = None) -> dict:
    """Create a display-safe summary of analysis mode, freshness, and quality."""
    settings = settings or get_settings()
    if prices.empty:
        return {
            "mode": settings.market_data_mode,
            "source_policy": SOURCE_POLICY_VERSION,
            "period_start": None,
            "period_end": None,
            "latest_fetched_at": None,
            "price_bases": [],
            "warning_count": len(warnings),
        }
    price_time = pd.to_datetime(prices["price_time"], utc=True, errors="coerce").dropna()
    fetched = pd.to_datetime(prices.get("fetched_at"), utc=True, errors="coerce").dropna()
    bases = sorted(str(value) for value in prices.get("price_basis", pd.Series(dtype=str)).dropna().unique())
    return {
        "mode": settings.market_data_mode,
        "source_policy": SOURCE_POLICY_VERSION,
        "period_start": price_time.min().to_pydatetime() if not price_time.empty else None,
        "period_end": price_time.max().to_pydatetime() if not price_time.empty else None,
        "latest_fetched_at": fetched.max().to_pydatetime() if not fetched.empty else None,
        "price_bases": bases,
        "warning_count": len(warnings),
    }


def build_us_japan_correlation_records(
    wide,
    computed_at: datetime | None = None,
    us_symbols: list[str] | None = None,
    japan_symbols: list[str] | None = None,
    input_provenance: dict | None = None,
) -> list[dict]:
    computed_at = computed_at or datetime.now(UTC)
    us_symbols = us_symbols or US_INDEX_SYMBOLS
    japan_symbols = japan_symbols or JAPAN_INDEX_SYMBOLS
    records: list[dict] = []
    provenance_fields = analysis_provenance_fields(input_provenance)
    pair_records_by_window: dict[int, list[dict]] = {window: [] for window in WINDOWS}
    pair_frames = []

    for us_symbol in us_symbols:
        for japan_symbol in japan_symbols:
            pair = us_japan_pair_frame(wide, us_symbol, japan_symbol, calendar_aware=True)
            if pair.empty:
                continue
            pair_frames.append(pair.assign(base_symbol=us_symbol, target_symbol=japan_symbol))
            horizons = horizon_correlations(pair, WINDOWS)
            for row in horizons.to_dict(orient="records"):
                window = int(row["window_days"])
                sample = pair.tail(window)
                if sample.empty:
                    continue
                period_start = pd.to_datetime(sample["japan_date"].iloc[0], utc=True).to_pydatetime()
                period_end = pd.to_datetime(sample["japan_date"].iloc[-1], utc=True).to_pydatetime()
                correlation = row["correlation"]
                record = {
                    "analysis_name": "us_japan_index_correlation",
                    "base_symbol": us_symbol,
                    "target_symbol": japan_symbol,
                    "window_days": window,
                    "method": "pearson",
                    "lag_rule": US_JAPAN_LAG_RULE,
                    "correlation": None if pd.isna(correlation) else float(correlation),
                    "sample_size": int(row["sample_size"]),
                    "period_start": period_start,
                    "period_end": period_end,
                    "computed_at": computed_at,
                    "source": "market_prices",
                    **{key: value for key, value in provenance_fields.items() if key != "input_provenance"},
                    "details": {
                        "base_market": "US",
                        "target_market": "JP",
                        "base_observation": "previous_trading_day_return",
                        "target_observation": "current_trading_day_return",
                        "input_provenance": provenance_fields["input_provenance"],
                    },
                }
                records.append(record)
                pair_records_by_window[window].append(record)

    for window, pair_records in pair_records_by_window.items():
        correlations = [record["correlation"] for record in pair_records if record["correlation"] is not None]
        if not pair_records:
            continue
        latest_period_end = max(record["period_end"] for record in pair_records)
        earliest_period_start = min(
            record["period_start"] for record in pair_records if record["period_start"] is not None
        )
        records.append(
            {
                "analysis_name": "us_japan_index_correlation_average",
                "base_symbol": "US_INDEX_AVERAGE",
                "target_symbol": "JP_INDEX_AVERAGE",
                "window_days": window,
                "method": "mean_of_pairwise_pearson",
                "lag_rule": US_JAPAN_LAG_RULE,
                "correlation": float(sum(correlations) / len(correlations)) if correlations else None,
                "sample_size": min(record["sample_size"] for record in pair_records),
                "period_start": earliest_period_start,
                "period_end": latest_period_end,
                "computed_at": computed_at,
                "source": "correlation_results",
                **{key: value for key, value in provenance_fields.items() if key != "input_provenance"},
                "details": {
                    "included_pairs": [
                        [record["base_symbol"], record["target_symbol"]] for record in pair_records
                    ],
                    "aggregation": "simple_mean_excluding_missing_correlations",
                    "note": "Correlation is a statistical tendency and does not imply causation.",
                    "input_provenance": provenance_fields["input_provenance"],
                },
            }
        )

    if pair_frames:
        group_pair = (
            pd.concat(pair_frames, ignore_index=True)
            .groupby("japan_date", as_index=False)
            .agg({"us_return": "mean", "japan_return": "mean"})
            .sort_values("japan_date")
        )
        for row in horizon_correlations(group_pair, WINDOWS).to_dict(orient="records"):
            window = int(row["window_days"])
            sample = group_pair.tail(window)
            if sample.empty:
                continue
            correlation = row["correlation"]
            records.append(
                {
                    "analysis_name": "us_japan_index_group_average_correlation",
                    "base_symbol": "US_INDEX_GROUP_MEAN",
                    "target_symbol": "JP_INDEX_GROUP_MEAN",
                    "window_days": window,
                    "method": "pearson_on_group_mean_returns",
                    "lag_rule": US_JAPAN_LAG_RULE,
                    "correlation": None if pd.isna(correlation) else float(correlation),
                    "sample_size": int(row["sample_size"]),
                    "period_start": pd.to_datetime(sample["japan_date"].iloc[0], utc=True).to_pydatetime(),
                    "period_end": pd.to_datetime(sample["japan_date"].iloc[-1], utc=True).to_pydatetime(),
                    "computed_at": computed_at,
                    "source": "market_prices",
                    **{key: value for key, value in provenance_fields.items() if key != "input_provenance"},
                    "details": {
                        "us_symbols": us_symbols,
                        "japan_symbols": japan_symbols,
                        "aggregation": "mean_daily_returns_before_correlation",
                        "note": "Correlation is a statistical tendency and does not imply causation.",
                        "input_provenance": provenance_fields["input_provenance"],
                    },
                }
            )

    return records


def persist_us_japan_correlation_results(session: Session, symbols: list[str] | None = None) -> int:
    prices = market_prices_frame(
        session, symbols or DEFAULT_SYMBOLS, source_policy=market_price_source_policy()
    )
    wide = close_wide(prices)
    records = build_us_japan_correlation_records(
        wide, input_provenance=build_analysis_input_provenance(prices)
    )
    count = upsert_correlation_results(session, records)
    session.commit()
    return count


def load_us_japan_spillover_analysis(session: Session, base_symbol: str, target_symbol: str) -> dict:
    """Load observed close-to-OHLC spillover data without imputing missing values."""
    prices = market_prices_frame(
        session, [base_symbol, target_symbol], source_policy=market_price_source_policy()
    )
    if prices.empty:
        frame = pd.DataFrame()
    else:
        base_prices = prices[prices["symbol"] == base_symbol].copy()
        target_prices = prices[prices["symbol"] == target_symbol].copy()
        if base_prices.empty or target_prices.empty:
            frame = pd.DataFrame()
        else:
            base_prices["price_time"] = pd.to_datetime(base_prices["price_time"], utc=True)
            base_close = (
                base_prices.sort_values("price_time")
                .drop_duplicates("price_time", keep="last")
                .set_index("price_time")["close"]
            )
            frame = us_japan_spillover_frame(base_close, target_prices, calendar_aware=True)
    return {
        "base_symbol": base_symbol,
        "target_symbol": target_symbol,
        "frame": frame,
        "conditional_stats": {
            metric: spillover_conditional_stats(frame, metric) for metric in TARGET_METRICS
        },
        "regression": {
            metric: build_spillover_regression_summary(frame, metric) for metric in TARGET_METRICS
        },
        "warnings": build_spillover_warnings(prices, base_symbol, target_symbol, frame),
        "input_provenance": build_analysis_input_provenance(prices),
    }


def load_sector_sensitivity_analysis(session: Session, base_symbol: str = "NASDAQCOM", limit: int = 200) -> dict:
    """Summarize observed spillover sensitivity by J-Quants sector and symbol."""
    assets = list_assets_by_source(session, "jquants", asset_types=["stock", "etf"], limit=limit)
    if not assets:
        return {"data": pd.DataFrame(), "sensitivity": {"sector": pd.DataFrame(), "symbol": pd.DataFrame()}}
    symbols = [asset.symbol for asset in assets]
    prices = market_prices_frame(session, [base_symbol, *symbols], source_policy=market_price_source_policy())
    base = prices[prices["symbol"] == base_symbol].copy()
    if base.empty:
        return {"data": pd.DataFrame(), "sensitivity": {"sector": pd.DataFrame(), "symbol": pd.DataFrame()}}
    base_close = base.sort_values("price_time").drop_duplicates("price_time").set_index("price_time")["close"]
    sector_by_symbol = {
        asset.symbol: (asset.metadata_json or {}).get("sector_33") or (asset.metadata_json or {}).get("sector_17") or "未分類"
        for asset in assets
    }
    rows = []
    for symbol in symbols:
        target = prices[prices["symbol"] == symbol]
        frame = us_japan_spillover_frame(base_close, target, calendar_aware=True)
        if frame.empty:
            continue
        rows.append(frame.assign(symbol=symbol, sector=sector_by_symbol.get(symbol, "未分類"))[ ["symbol", "sector", "us_return", "daily_return"] ].rename(columns={"daily_return": "target_return"}))
    data = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    return {"data": data, "sensitivity": sector_sensitivity(data)}


def load_asset_screening_analysis(session: Session, limit: int = 200) -> dict:
    """Load a bounded, source-policy-selected stock/ETF technical screen."""
    assets = list_assets_by_source(session, "jquants", asset_types=["stock", "etf"], limit=limit)
    if not assets:
        return {"assets": pd.DataFrame(), "prices": pd.DataFrame(), "screening": pd.DataFrame()}
    symbols = [asset.symbol for asset in assets]
    prices = market_prices_frame(session, symbols, source_policy=market_price_source_policy())
    asset_rows = pd.DataFrame(
        [
            {
                "symbol": asset.symbol,
                "name": asset.name,
                "asset_type": asset.asset_type,
                "metadata_json": asset.metadata_json or {},
            }
            for asset in assets
        ]
    )
    return {"assets": asset_rows, "prices": prices, "screening": screen_assets(prices, asset_rows)}


def build_spillover_warnings(
    prices: pd.DataFrame, base_symbol: str, target_symbol: str, frame: pd.DataFrame
) -> list[str]:
    if prices.empty:
        return ["米国指数または日本株・ETFの価格データがありません。"]
    warnings: list[str] = []
    base = prices[prices["symbol"] == base_symbol]
    target = prices[prices["symbol"] == target_symbol]
    if base.empty:
        warnings.append(f"{base_symbol} の終値データがありません。")
    if target.empty:
        warnings.append(f"{target_symbol} のJ-Quants OHLCデータがありません。")
    elif target[["open", "close"]].dropna().empty:
        warnings.append(f"{target_symbol} は始値・終値がそろわないため、波及分析を計算できません。")
    if frame.empty and not warnings:
        warnings.append("前営業日の米国終値と日本当日のOHLCを対応できる日がまだ不足しています。")
    return warnings


def build_us_japan_spillover_feature_records(
    frame: pd.DataFrame,
    base_symbol: str,
    target_symbol: str,
    computed_at: datetime | None = None,
    input_provenance: dict | None = None,
) -> list[dict]:
    computed_at = computed_at or datetime.now(UTC)
    if frame.empty:
        return []
    records: list[dict] = []
    provenance_fields = analysis_provenance_fields(input_provenance)
    for row in frame.itertuples(index=False):
        for metric in TARGET_METRICS:
            value = getattr(row, metric)
            if pd.isna(value) or pd.isna(row.us_return):
                continue
            records.append(
                {
                    "base_symbol": base_symbol,
                    "target_symbol": target_symbol,
                    "japan_session_date": pd.Timestamp(row.japan_date).date(),
                    "us_session_date": pd.Timestamp(row.us_date).date(),
                    "metric": metric,
                    "us_return": float(row.us_return),
                    "target_return": float(value),
                    "lag_rule": US_JAPAN_LAG_RULE,
                    "computed_at": computed_at,
                    **{key: value for key, value in provenance_fields.items() if key != "input_provenance"},
                    "details": {
                        "base_observation": "US close-to-close return",
                        "target_observation": metric,
                        "base_source_constraint": "close_only_no_intraday_inference",
                        "target_source_constraint": "observed_jquants_ohlc_only",
                        "input_provenance": provenance_fields["input_provenance"],
                    },
                }
            )
    return records


def build_spillover_regression_summary(
    frame: pd.DataFrame, target_metric: str, windows: list[int] | None = None
) -> dict:
    """Estimate only trailing and contemporaneously available lagged regressions."""
    windows = windows or [20, 60, 120, 250]
    if frame.empty or target_metric not in TARGET_METRICS:
        return {
            "full": {"status": "insufficient_data", "sample_size": 0},
            "windows": [],
            "rolling": {},
            "walk_forward": pd.DataFrame(),
            "granger": {"status": "insufficient_data", "sample_size": 0, "max_lag": 5},
        }
    data = frame.set_index("japan_date")
    features = data[["us_return"]]
    target = data[target_metric]
    summaries = []
    rolling = {}
    for window in windows:
        sample_features = features.tail(window)
        sample_target = target.tail(window)
        result = run_ols(sample_features, sample_target)
        summaries.append({"window_days": window, **result})
        rolling[window] = rolling_ols(features, target, window)
    return {
        "full": run_ols(features, target),
        "windows": summaries,
        "rolling": rolling,
        "walk_forward": walk_forward_ols(features, target, min_train_size=max(20, len(features.columns) + 9)),
        "granger": run_granger_test(features["us_return"], target),
    }


def build_us_japan_spillover_model_records(
    analysis: dict, computed_at: datetime | None = None
) -> list[dict]:
    computed_at = computed_at or datetime.now(UTC)
    frame = analysis["frame"]
    if frame.empty:
        return []
    records: list[dict] = []
    provenance_fields = analysis_provenance_fields(analysis.get("input_provenance"))
    for metric, summary in analysis["regression"].items():
        for analysis_name, window_days, result, sample in [
            ("us_japan_spillover_lag_ols", 0, summary["full"], frame),
            *[
                ("us_japan_spillover_lag_ols", row["window_days"], row, frame.tail(row["window_days"]))
                for row in summary["windows"]
            ],
        ]:
            if result["status"] != "ok":
                continue
            records.append(
                _spillover_model_record(
                    analysis_name,
                    analysis["base_symbol"],
                    analysis["target_symbol"],
                    metric,
                    window_days,
                    sample,
                    result,
                    computed_at,
                    provenance_fields,
                )
            )
        for window_days, rolling in summary["rolling"].items():
            if rolling.empty:
                continue
            last = rolling.iloc[-1]
            sample = frame[frame["japan_date"] <= last["period_end"]].tail(window_days)
            records.append(
                _spillover_model_record(
                    "us_japan_spillover_rolling_ols",
                    analysis["base_symbol"],
                    analysis["target_symbol"],
                    metric,
                    window_days,
                    sample,
                    {
                        "sample_size": int(last["sample_size"]),
                        "r_squared": float(last["r_squared"]),
                        "coefficients": {"us_return": float(last["us_return"])},
                        "covariance_type": "HAC",
                        "hac_lags": 3,
                    },
                    computed_at,
                    provenance_fields,
                )
            )
        granger = summary["granger"]
        if granger["status"] == "ok":
            valid = frame.dropna(subset=["us_return", metric])
            records.append(
                {
                    "analysis_name": "us_japan_spillover_granger",
                    "base_symbol": analysis["base_symbol"],
                    "target_symbol": analysis["target_symbol"],
                    "target_metric": metric,
                    "window_days": 0,
                    "method": "granger_ssr_ftest",
                    "sample_size": granger["sample_size"],
                    "r_squared": None,
                    "period_start": pd.Timestamp(valid["japan_date"].iloc[0]).to_pydatetime(),
                    "period_end": pd.Timestamp(valid["japan_date"].iloc[-1]).to_pydatetime(),
                    "computed_at": computed_at,
                    "model_version": SPILLOVER_GRANGER_VERSION,
                    **{key: value for key, value in provenance_fields.items() if key != "input_provenance"},
                    "details": {
                        "lag_rule": US_JAPAN_LAG_RULE,
                        "max_lag": granger["max_lag"],
                        "lag_results": granger["lag_results"],
                        "minimum_p_value": granger["minimum_p_value"],
                        "multiple_comparison_method": "bonferroni_by_tested_lags",
                        "note": "Granger testing indicates predictive precedence only; it does not prove causation or provide investment advice.",
                        "input_provenance": provenance_fields["input_provenance"],
                    },
                }
            )
    return records


def _spillover_model_record(
    analysis_name: str,
    base_symbol: str,
    target_symbol: str,
    metric: str,
    window_days: int,
    sample: pd.DataFrame,
    result: dict,
    computed_at: datetime,
    provenance_fields: dict,
) -> dict:
    valid = sample.dropna(subset=["us_return", metric])
    return {
        "analysis_name": analysis_name,
        "base_symbol": base_symbol,
        "target_symbol": target_symbol,
        "target_metric": metric,
        "window_days": window_days,
        "method": "ols",
        "sample_size": int(result["sample_size"]),
        "r_squared": result.get("r_squared"),
        "period_start": pd.Timestamp(valid["japan_date"].iloc[0]).to_pydatetime(),
        "period_end": pd.Timestamp(valid["japan_date"].iloc[-1]).to_pydatetime(),
        "computed_at": computed_at,
        "model_version": SPILLOVER_MODEL_VERSION,
        **{key: value for key, value in provenance_fields.items() if key != "input_provenance"},
        "details": {
            "lag_rule": US_JAPAN_LAG_RULE,
            "coefficients": result.get("coefficients", {}),
            "p_values": result.get("p_values", {}),
            "confidence_intervals_95": result.get("confidence_intervals_95", {}),
            "covariance_type": result.get("covariance_type"),
            "hac_lags": result.get("hac_lags"),
            "note": "This is a statistical association, not proof of causation or an investment recommendation.",
            "input_provenance": provenance_fields["input_provenance"],
        },
    }


def persist_us_japan_spillover_features(
    session: Session, base_symbol: str, target_symbol: str
) -> tuple[int, dict]:
    analysis = load_us_japan_spillover_analysis(session, base_symbol, target_symbol)
    records = build_us_japan_spillover_feature_records(
        analysis["frame"], base_symbol, target_symbol, input_provenance=analysis["input_provenance"]
    )
    count = upsert_spillover_features(session, records)
    saved_models = upsert_spillover_model_results(
        session, build_us_japan_spillover_model_records(analysis)
    )
    session.commit()
    analysis["saved_model_results"] = saved_models
    return count, analysis


def load_short_term_analysis(session: Session, symbol: str) -> dict:
    prices = market_prices_frame(session, [symbol], source_policy=market_price_source_policy())
    if prices.empty:
        return {"prices": prices, "indicators": pd.DataFrame(), "snapshot": short_term_signal_snapshot(pd.DataFrame())}

    frame = prices.copy()
    frame["price_time"] = pd.to_datetime(frame["price_time"], utc=True).dt.normalize()
    frame["close"] = pd.to_numeric(frame["close"])
    close = frame.drop_duplicates("price_time").set_index("price_time")["close"].sort_index()
    indicators = short_term_indicator_frame(close)
    return {
        "prices": frame,
        "indicators": indicators,
        "snapshot": short_term_signal_snapshot(indicators),
        "source": frame["source"].dropna().iloc[-1] if not frame["source"].dropna().empty else "-",
        "fetched_at": frame["fetched_at"].dropna().iloc[-1] if not frame["fetched_at"].dropna().empty else None,
    }


def load_movement_and_virtual_trade_analysis(
    session: Session,
    candidate_limit: int = 20,
    virtual_trade_limit: int = 50,
    score_threshold: int = 70,
    holding_days: int = 5,
) -> dict:
    index_prices = market_prices_frame(
        session, DEFAULT_SYMBOLS, source_policy=market_price_source_policy()
    )
    jquants_assets = list_assets_by_source(session, "jquants", asset_types=["stock", "etf"])
    japan_symbols = [asset.symbol for asset in jquants_assets]
    japan_prices = (
        market_prices_frame(session, japan_symbols, source_policy=market_price_source_policy())
        if japan_symbols
        else pd.DataFrame()
    )
    virtual_trades = build_virtual_trades(
        index_prices,
        japan_prices,
        score_threshold=score_threshold,
        holding_days=holding_days,
        max_trades=virtual_trade_limit,
    )
    feedback = summarize_virtual_trade_feedback(virtual_trades)
    candidates = build_movement_candidates(index_prices, japan_prices, limit=candidate_limit, feedback_by_symbol=feedback)
    return {
        "index_prices": index_prices,
        "japan_prices": japan_prices,
        "asset_count": len(japan_symbols),
        "movement": candidates,
        "virtual_trades": virtual_trades,
        "virtual_feedback": feedback,
        "score_threshold": score_threshold,
        "holding_days": holding_days,
    }
