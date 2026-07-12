from datetime import UTC, datetime

import pandas as pd

from app.providers.fred import FredMarketProvider
from app.services.analysis_service import build_data_quality_warnings


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
