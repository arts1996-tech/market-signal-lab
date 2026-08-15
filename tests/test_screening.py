import pandas as pd

from app.analysis.market_calendar import exchange_calendar
from app.analysis.screening import screen_assets, technical_attention_snapshot


def _xtks_sessions(start: str, end: str, count: int) -> pd.DatetimeIndex:
    return exchange_calendar("XTKS").sessions_in_range(start, end)[:count]


def test_screen_assets_requires_history_and_preserves_asset_type():
    dates = _xtks_sessions("2024-01-01", "2024-05-31", 55)
    prices = pd.DataFrame({"symbol": "13060", "price_time": dates, "close": range(100, 155)})
    assets = pd.DataFrame([{"symbol": "13060", "name": "ETF", "asset_type": "etf", "metadata_json": {"sector_33": "ETF"}}])

    result = screen_assets(prices, assets, min_history=50)

    assert len(result) == 1
    assert result.iloc[0]["asset_type"] == "etf"
    assert result.iloc[0]["sector"] == "ETF"


def test_screen_assets_default_gate_is_30_distinct_valid_observations():
    dates = _xtks_sessions("2024-01-01", "2024-03-31", 30)
    prices = pd.DataFrame(
        {
            "symbol": "13060",
            "price_time": dates,
            "close": range(100, 130),
            "price_basis": "raw_ohlcv_with_adjusted",
        }
    )
    assets = pd.DataFrame(
        [
            {
                "symbol": "13060",
                "name": "ETF",
                "asset_type": "etf",
                "metadata_json": {},
            }
        ]
    )

    result = screen_assets(prices, assets)

    assert len(result) == 1
    assert result.iloc[0]["observations"] == 30
    assert result.iloc[0]["price_basis"] == "raw_ohlcv_with_adjusted"
    assert result.iloc[0]["data_as_of"] == pd.to_datetime(dates[-1], utc=True)

    duplicate_only = pd.concat([prices.iloc[:-1], prices.iloc[[-2]]], ignore_index=True)
    assert screen_assets(duplicate_only, assets).empty


def test_screen_assets_rejects_observation_count_that_crosses_a_session_gap():
    old_dates = _xtks_sessions("2024-01-01", "2024-03-31", 20)
    recent_dates = _xtks_sessions("2026-04-01", "2026-05-31", 15)
    dates = old_dates.append(recent_dates)
    prices = pd.DataFrame(
        {
            "symbol": "13060",
            "price_time": dates,
            "close": range(100, 135),
            "price_basis": "raw_ohlcv_with_adjusted",
        }
    )
    assets = pd.DataFrame(
        [{"symbol": "13060", "name": "ETF", "asset_type": "etf", "metadata_json": {}}]
    )

    assert screen_assets(prices, assets).empty


def test_attention_score_is_explainable_and_not_a_trade_label():
    snapshot = technical_attention_snapshot(
        pd.Series(
            {
                "close": 120,
                "return_20d": 0.12,
                "volatility_20d": 0.035,
                "rsi_14": 75,
                "bb_upper": 119,
                "bb_lower": 90,
                "histogram": 1.5,
            }
        ),
        observations=30,
    )

    assert snapshot["attention_score"] == 100
    assert snapshot["attention_label"] == "高い注目度"
    assert any("20日騰落率" in reason for reason in snapshot["attention_reasons"])
    assert "50日・75日移動平均は未算出" in snapshot["quality_warnings"]
    assert "買い" not in snapshot["attention_label"]
