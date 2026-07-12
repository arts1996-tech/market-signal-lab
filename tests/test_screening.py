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
