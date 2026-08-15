import pandas as pd

from app.analysis.screening import screen_assets


def test_screen_assets_requires_history_and_preserves_asset_type():
    dates = pd.date_range("2024-01-01", periods=55, tz="UTC")
    prices = pd.DataFrame({"symbol": "13060", "price_time": dates, "close": range(100, 155)})
    assets = pd.DataFrame([{"symbol": "13060", "name": "ETF", "asset_type": "etf", "metadata_json": {"sector_33": "ETF"}}])

    result = screen_assets(prices, assets, min_history=50)

    assert len(result) == 1
    assert result.iloc[0]["asset_type"] == "etf"
    assert result.iloc[0]["sector"] == "ETF"


def test_screen_assets_default_gate_is_30_distinct_valid_observations():
    dates = pd.date_range("2024-01-01", periods=30, tz="UTC")
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
    assert result.iloc[0]["data_as_of"] == dates[-1]

    duplicate_only = pd.concat([prices.iloc[:-1], prices.iloc[[-2]]], ignore_index=True)
    assert screen_assets(duplicate_only, assets).empty
