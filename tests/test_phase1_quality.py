from datetime import UTC, datetime

import pandas as pd

from app.analysis.market_calendar import calendar_gap_report, next_exchange_session
from app.providers.fred import FredMarketProvider
from app.core.config import Settings
from app.services.analysis_service import build_analysis_status, build_data_quality_warnings


def test_fred_provider_exposes_phase1_common_interface():
    provider = FredMarketProvider()

    assert provider.fetch_assets()
    assert provider.fetch_fundamentals("SP500").empty
    assert provider.fetch_events("SP500").empty
    assert provider.health_check()["provider"] == "fred"


def test_data_quality_warning_marks_stale_price():
    prices = pd.DataFrame(
        [
            {
                "symbol": "SP500",
                "price_time": pd.Timestamp("2026-01-01", tz="UTC"),
            }
        ]
    )

    warnings = build_data_quality_warnings(
        prices,
        stale_after_days=7,
        now=datetime(2026, 1, 10, tzinfo=UTC),
    )

    assert warnings[0]["status"] == "stale"
    assert warnings[0]["age_days"] == 9


def test_data_quality_warning_accepts_recent_price():
    prices = pd.DataFrame(
        [
            {
                "symbol": "SP500",
                "price_time": pd.Timestamp("2026-01-09", tz="UTC"),
            }
        ]
    )

    assert not build_data_quality_warnings(
        prices,
        stale_after_days=7,
        now=datetime(2026, 1, 10, tzinfo=UTC),
    )


def test_analysis_status_labels_demo_source_policy_without_real_data_claims():
    status = build_analysis_status(
        pd.DataFrame(),
        [{"status": "missing", "message": "demo"}],
        settings=Settings(market_data_mode="demo"),
    )

    assert status["mode"] == "demo"
    assert status["source_policy"] == "demo_only"


def test_calendar_gap_report_detects_jpx_holiday_as_missing_session_only_when_expected():
    report = calendar_gap_report(
        pd.to_datetime(["2024-01-04", "2024-01-05", "2024-01-09"], utc=True), "XTKS"
    )

    assert report["missing_sessions"] == []
    assert report["unexpected_sessions"] == []


def test_calendar_out_of_bounds_date_is_skipped():
    assert next_exchange_session(pd.Timestamp("1971-02-08", tz="UTC"), "XTKS") is None
