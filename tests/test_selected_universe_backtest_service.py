import pandas as pd

from app.analysis.market_calendar import exchange_calendar
from app.services.selected_universe_backtest_service import (
    SELECTED_UNIVERSE_BACKTEST_RULES,
    selected_asset_eligibility_rows,
)


def test_selected_universe_backtest_rejects_unanalyzed_and_cross_market_assets_without_dropping_them():
    assets = [
        {
            "asset_id": "jp-asset",
            "symbol": "1306",
            "exchange": "JPX",
            "currency": "JPY",
            "analysis_status": "analyzed",
            "analysis_reasons": [],
        },
        {
            "asset_id": "us-asset",
            "symbol": "NVDA",
            "exchange": "NASDAQ",
            "currency": "USD",
            "analysis_status": "analyzed",
            "analysis_reasons": [],
        },
        {
            "asset_id": "missing-analysis",
            "symbol": "9999",
            "exchange": "JPX",
            "currency": "JPY",
            "analysis_status": "insufficient_data",
            "analysis_reasons": ["not_eligible_in_source_analysis_run"],
        },
    ]
    prices = pd.DataFrame()

    rows = selected_asset_eligibility_rows(
        assets, prices, rule=SELECTED_UNIVERSE_BACKTEST_RULES["short_term"]
    )

    assert [row["asset_id"] for row in rows] == ["jp-asset", "us-asset", "missing-analysis"]
    assert all(row["status"] == "insufficient_data" for row in rows)
    assert "cross_market_cash_simulation_not_yet_supported" in rows[1]["reason_codes"]
    assert "analysis_snapshot_not_eligible" in rows[2]["reason_codes"]


def test_selected_universe_backtest_accepts_only_selected_jpx_cash_asset_after_full_history_gate():
    sessions = exchange_calendar("XTKS").sessions_in_range("2026-01-05", "2026-06-30")[:85]
    selected = {
        "asset_id": "jp-asset",
        "symbol": "1306",
        "exchange": "JPX",
        "currency": "JPY",
        "analysis_status": "analyzed",
        "analysis_reasons": [],
    }
    prices = pd.DataFrame(
        [
            {
                "symbol": "1306",
                "price_time": session,
                "open": 100,
                "high": 102,
                "low": 99,
                "close": 101,
                "volume": 1_000_000,
            }
            for session in sessions
        ]
        + [
            {
                "symbol": "outside",
                "price_time": session,
                "open": 1,
                "high": 2,
                "low": 1,
                "close": 1,
                "volume": 1,
            }
            for session in sessions
        ]
    )

    rows = selected_asset_eligibility_rows(
        [selected], prices, rule=SELECTED_UNIVERSE_BACKTEST_RULES["short_term"]
    )

    assert rows[0]["status"] == "eligible"
    assert rows[0]["contiguous_sessions"] >= 80
