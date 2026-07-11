from datetime import UTC, datetime

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
from app.database.repositories import market_prices_frame, upsert_correlation_results


DEFAULT_SYMBOLS = ["NASDAQCOM", "DJIA", "SP500", "NIKKEI225", "DEXJPUS"]
US_INDEX_SYMBOLS = ["NASDAQCOM", "DJIA", "SP500"]
JAPAN_INDEX_SYMBOLS = ["NIKKEI225"]
US_JAPAN_LAG_RULE = "us_previous_trading_day_to_japan_current_day"


def load_market_analysis(session: Session, symbols: list[str] | None = None) -> dict:
    prices = market_prices_frame(session, symbols or DEFAULT_SYMBOLS)
    wide = close_wide(prices)
    pair = us_japan_pair_frame(wide, "NASDAQCOM", "NIKKEI225")
    return {
        "prices": prices,
        "wide": wide,
        "normalized": normalized_index(wide),
        "pair": pair,
        "horizon_correlations": horizon_correlations(pair),
        "rolling_correlation": rolling_correlation(pair, 60),
        "conditional_stats": conditional_next_day_stats(pair),
    }


def build_us_japan_correlation_records(
    wide,
    computed_at: datetime | None = None,
    us_symbols: list[str] | None = None,
    japan_symbols: list[str] | None = None,
) -> list[dict]:
    computed_at = computed_at or datetime.now(UTC)
    us_symbols = us_symbols or US_INDEX_SYMBOLS
    japan_symbols = japan_symbols or JAPAN_INDEX_SYMBOLS
    records: list[dict] = []
    pair_records_by_window: dict[int, list[dict]] = {window: [] for window in WINDOWS}
    pair_frames = []

    for us_symbol in us_symbols:
        for japan_symbol in japan_symbols:
            pair = us_japan_pair_frame(wide, us_symbol, japan_symbol)
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
                    "details": {
                        "base_market": "US",
                        "target_market": "JP",
                        "base_observation": "previous_trading_day_return",
                        "target_observation": "current_trading_day_return",
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
                "details": {
                    "included_pairs": [
                        [record["base_symbol"], record["target_symbol"]] for record in pair_records
                    ],
                    "aggregation": "simple_mean_excluding_missing_correlations",
                    "note": "Correlation is a statistical tendency and does not imply causation.",
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
                    "details": {
                        "us_symbols": us_symbols,
                        "japan_symbols": japan_symbols,
                        "aggregation": "mean_daily_returns_before_correlation",
                        "note": "Correlation is a statistical tendency and does not imply causation.",
                    },
                }
            )

    return records


def persist_us_japan_correlation_results(session: Session, symbols: list[str] | None = None) -> int:
    prices = market_prices_frame(session, symbols or DEFAULT_SYMBOLS)
    wide = close_wide(prices)
    records = build_us_japan_correlation_records(wide)
    count = upsert_correlation_results(session, records)
    session.commit()
    return count
