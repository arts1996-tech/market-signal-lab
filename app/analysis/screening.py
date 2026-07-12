"""Deterministic stock/ETF screening primitives for phase 3."""

import pandas as pd

from app.analysis.technical import short_term_indicator_frame


def screen_assets(prices: pd.DataFrame, assets: pd.DataFrame, min_history: int = 50) -> pd.DataFrame:
    """Build a technical screening table; fundamentals are intentionally not inferred."""
    columns = ["symbol", "name", "asset_type", "sector", "latest_close", "return_20d", "volatility_20d", "rsi_14"]
    if prices.empty or assets.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    for asset in assets.itertuples(index=False):
        sample = prices[prices["symbol"] == asset.symbol].copy().sort_values("price_time")
        if len(sample) < min_history:
            continue
        close = pd.to_numeric(sample["close"], errors="coerce").dropna()
        indicators = short_term_indicator_frame(close)
        if indicators.empty:
            continue
        latest = indicators.dropna(subset=["close"]).iloc[-1]
        metadata = getattr(asset, "metadata_json", {}) or {}
        rows.append({
            "symbol": asset.symbol,
            "name": asset.name,
            "asset_type": asset.asset_type,
            "sector": metadata.get("sector_33") or metadata.get("sector_17") or "未分類",
            "latest_close": float(latest["close"]),
            "return_20d": latest.get("return_20d"),
            "volatility_20d": latest.get("volatility_20d"),
            "rsi_14": latest.get("rsi_14"),
        })
    return pd.DataFrame(rows, columns=columns).sort_values("return_20d", ascending=False)
