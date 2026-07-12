from datetime import UTC, datetime

import pandas as pd
import pytest

from app.analysis.spillover import (
    japan_session_returns,
    spillover_conditional_stats,
    us_japan_spillover_frame,
)
from app.services.analysis_service import (
    build_spillover_regression_summary,
    build_us_japan_spillover_feature_records,
    build_us_japan_spillover_model_records,
)


def _japan_prices() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "price_time": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"], utc=True),
            "open": [200, 201, 204],
            "close": [200, 202, 205],
        }
    )


def test_japan_session_returns_keep_gap_intraday_and_daily_separate():
    sessions = japan_session_returns(_japan_prices())

    row = sessions.iloc[-1]
    assert row["gap_return"] == 204 / 202 - 1
    assert row["intraday_return"] == 205 / 204 - 1
    assert row["daily_return"] == 205 / 202 - 1


def test_japan_session_returns_excludes_unverified_weekday_gap_but_keeps_intraday():
    prices = pd.DataFrame(
        {
            "price_time": pd.to_datetime(["2024-01-05", "2024-01-09"], utc=True),
            "open": [100, 110],
            "close": [101, 111],
        }
    )

    sessions = japan_session_returns(prices)
    row = sessions.iloc[-1]

    assert pd.isna(row["gap_return"])
    assert pd.isna(row["daily_return"])
    assert row["intraday_return"] == 111 / 110 - 1


def test_us_japan_spillover_uses_strictly_earlier_us_session():
    us_close = pd.Series(
        [100, 102, 101],
        index=pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"], utc=True),
    )

    frame = us_japan_spillover_frame(us_close, _japan_prices())

    row = frame.iloc[-1]
    assert row["japan_date"] == pd.Timestamp("2024-01-04", tz="UTC")
    assert row["us_date"] == pd.Timestamp("2024-01-03", tz="UTC")
    assert row["us_return"] == pytest.approx(0.02)
    assert row["gap_return"] == 204 / 202 - 1


def test_calendar_aware_spillover_accumulates_us_sessions_over_jpx_holiday():
    us_close = pd.Series(
        [100, 101, 102.01, 103.0301, 104.060401],
        index=pd.to_datetime(["2026-05-01", "2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07"], utc=True),
    )
    japan_prices = pd.DataFrame(
        {"price_time": pd.to_datetime(["2026-05-01", "2026-05-07"], utc=True), "open": [200, 204], "close": [201, 205]}
    )

    frame = us_japan_spillover_frame(us_close, japan_prices, calendar_aware=True)

    assert frame.iloc[-1]["us_return"] == pytest.approx(0.030301)


def test_conditional_stats_and_feature_records_use_observed_metrics_only():
    frame = pd.DataFrame(
        {
            "japan_date": pd.to_datetime(["2024-01-04", "2024-01-05"], utc=True),
            "us_date": pd.to_datetime(["2024-01-03", "2024-01-04"], utc=True),
            "us_return": [0.02, -0.015],
            "gap_return": [0.01, None],
            "intraday_return": [0.002, -0.003],
            "daily_return": [0.012, -0.004],
        }
    )

    stats = spillover_conditional_stats(frame, "gap_return")
    records = build_us_japan_spillover_feature_records(
        frame, "NASDAQCOM", "13060", computed_at=datetime(2024, 1, 6, tzinfo=UTC)
    )

    assert stats["sample_size"].sum() == 1
    assert len(records) == 5
    assert {record["metric"] for record in records} == {
        "gap_return",
        "intraday_return",
        "daily_return",
    }
    assert all(record["lag_rule"] == "us_previous_trading_day_to_japan_current_day" for record in records)
    assert all(record["analysis_status"] == "current" for record in records)


def test_spillover_regression_builds_lagged_and_trailing_results():
    dates = pd.date_range("2024-01-01", periods=25, tz="UTC")
    frame = pd.DataFrame(
        {
            "japan_date": dates,
            "us_date": dates - pd.Timedelta(days=1),
            "us_return": [value / 1000 for value in range(25)],
        }
    )
    frame["gap_return"] = frame["us_return"] * 0.5
    frame["intraday_return"] = frame["us_return"] * -0.25
    frame["daily_return"] = frame["gap_return"] + frame["intraday_return"]

    summary = build_spillover_regression_summary(frame, "gap_return")
    records = build_us_japan_spillover_model_records(
        {"frame": frame, "base_symbol": "NASDAQCOM", "target_symbol": "13060", "regression": {"gap_return": summary}},
        computed_at=datetime(2024, 2, 1, tzinfo=UTC),
    )

    assert summary["full"]["status"] == "ok"
    assert summary["full"]["coefficients"]["us_return"] == pytest.approx(0.5)
    assert not summary["rolling"][20].empty
    assert not summary["walk_forward"].empty
    assert {record["analysis_name"] for record in records} == {
        "us_japan_spillover_lag_ols",
        "us_japan_spillover_rolling_ols",
    }
    assert all(record["model_version"] == "ols_us_return_v1" for record in records)
    assert all(record["details"]["covariance_type"] == "HAC" for record in records)
