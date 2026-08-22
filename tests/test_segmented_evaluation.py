import pandas as pd
import pytest

from app.analysis.market_calendar import exchange_calendar
from app.backtest.segmented_evaluation import (
    SegmentedEvaluationPolicy,
    classify_completed_trades,
    summarize_segmented_trades,
)


def _sessions(count: int = 70) -> pd.DatetimeIndex:
    return exchange_calendar("XTKS").sessions_in_range(
        "2025-01-01", "2025-12-31"
    )[:count]


def _index_prices() -> pd.DataFrame:
    sessions = _sessions()
    rows = []
    for location, session in enumerate(sessions):
        rows.extend(
            [
                {
                    "symbol": "NIKKEI225",
                    "price_time": session,
                    "close": 30_000 * (1.004**location),
                },
                {
                    "symbol": "DEXJPUS",
                    "price_time": session,
                    "close": 140 * (1.002**location),
                },
            ]
        )
    return pd.DataFrame(rows)


def _simulation_result() -> dict:
    sessions = _sessions()
    return {
        "manifest": {"run_id": "run-a"},
        "transactions": pd.DataFrame(
            [
                {
                    "action": "利益確定",
                    "symbol": "10010",
                    "date": sessions[35],
                    "decision_as_of": sessions[30],
                    "realized_pnl": 10_000,
                    "trade_return": 0.04,
                    "score": 85,
                    "sector": "情報・通信業",
                    "previous_turnover": 1_200_000_000,
                }
            ]
        ),
    }


def test_completed_trade_is_classified_at_decision_time():
    trades = classify_completed_trades(
        _simulation_result(),
        _index_prices(),
        validation_window=2,
    )

    assert len(trades) == 1
    row = trades.iloc[0]
    assert row["validation_window"] == 2
    assert row["market_direction"] == "uptrend"
    assert row["volatility_regime"] == "low_volatility"
    assert row["fx_regime"] == "yen_weakening"
    assert row["sector"] == "情報・通信業"
    assert row["liquidity_band"] == "high"
    assert row["score_band"] == "80_89"


def test_future_index_and_fx_rows_do_not_change_trade_classification():
    full = _index_prices()
    decision = _sessions()[30].tz_localize("UTC")
    historical = full[
        pd.to_datetime(full["price_time"], utc=True) <= decision
    ].copy()
    future = full[
        pd.to_datetime(full["price_time"], utc=True) > decision
    ].copy()
    future.loc[future["symbol"] == "NIKKEI225", "close"] *= 0.1
    future.loc[future["symbol"] == "DEXJPUS", "close"] *= 10

    historical_result = classify_completed_trades(
        _simulation_result(), historical
    )
    extended_result = classify_completed_trades(
        _simulation_result(), pd.concat([historical, future], ignore_index=True)
    )

    columns = [
        "market_direction",
        "market_return_20d",
        "volatility_regime",
        "market_daily_volatility_20d",
        "fx_regime",
        "usd_jpy_return_20d",
    ]
    assert historical_result.loc[0, columns].to_dict() == extended_result.loc[
        0, columns
    ].to_dict()


def test_small_segment_is_not_performance_assessed():
    trades = classify_completed_trades(_simulation_result(), _index_prices())

    result = summarize_segmented_trades(trades)
    overall = next(
        row for row in result["summaries"] if row["segment_dimension"] == "overall"
    )

    assert result["completed_trades"] == 1
    assert overall["sample_status"] == "insufficient_sample"
    assert overall["performance_assessment"] == "not_assessed_small_sample"
    assert overall["win_rate_ci95"] is not None
    assert overall["average_trade_return_ci95"] is None
    assert "small_sample_segments_are_not_performance_assessed" in result["warnings"]


def test_missing_current_market_or_stale_fx_is_not_backfilled():
    index_prices = _index_prices()
    decision = _sessions()[30].tz_localize("UTC")
    times = pd.to_datetime(index_prices["price_time"], utc=True)
    missing_current_market = index_prices[
        ~(
            index_prices["symbol"].eq("NIKKEI225")
            & times.eq(decision)
        )
        & ~(
            index_prices["symbol"].eq("DEXJPUS")
            & times.gt(decision - pd.Timedelta(days=5))
        )
    ].copy()

    trades = classify_completed_trades(
        _simulation_result(), missing_current_market
    )

    assert trades.iloc[0]["market_direction"] == "data_unavailable"
    assert trades.iloc[0]["volatility_regime"] == "data_unavailable"
    assert trades.iloc[0]["fx_regime"] == "data_unavailable"


def test_sufficient_positive_segment_uses_return_confidence_interval():
    policy = SegmentedEvaluationPolicy(minimum_assessment_trades=30)
    rows = []
    for index in range(30):
        rows.append(
            {
                "trade_id": f"trade-{index}",
                "trade_return": 0.01 + index / 100_000,
                "realized_pnl": 1_000 + index,
                "market_direction": "uptrend",
                "volatility_regime": "low_volatility",
                "fx_regime": "fx_neutral",
                "sector": "test",
                "liquidity_band": "medium",
                "score_band": "70_79",
            }
        )

    result = summarize_segmented_trades(pd.DataFrame(rows), policy=policy)
    overall = result["summaries"][0]

    assert overall["trade_count"] == 30
    assert overall["sample_status"] == "assessment_allowed"
    assert overall["performance_assessment"] == "positive_observed"
    assert overall["average_trade_return_ci95"][0] > 0


def test_duplicate_trade_identity_is_rejected():
    trades = pd.DataFrame(
        [
            {"trade_id": "same", "trade_return": 0.01, "realized_pnl": 100},
            {"trade_id": "same", "trade_return": -0.01, "realized_pnl": -100},
        ]
    )

    with pytest.raises(ValueError, match="unique across validation windows"):
        summarize_segmented_trades(trades)
