"""Deterministic stock/ETF screening primitives for phase 3."""

import pandas as pd

from app.analysis.technical import short_term_indicator_frame


SCREENING_MIN_HISTORY = 30


def screen_assets(
    prices: pd.DataFrame,
    assets: pd.DataFrame,
    min_history: int = SCREENING_MIN_HISTORY,
) -> pd.DataFrame:
    """Build a technical screening table; fundamentals are intentionally not inferred."""
    columns = [
        "symbol",
        "name",
        "asset_type",
        "sector",
        "observations",
        "data_as_of",
        "price_basis",
        "latest_close",
        "return_20d",
        "volatility_20d",
        "rsi_14",
    ]
    if prices.empty or assets.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    for asset in assets.itertuples(index=False):
        sample = prices[prices["symbol"] == asset.symbol].copy()
        sample["price_time"] = pd.to_datetime(sample["price_time"], utc=True, errors="coerce")
        sample["close"] = pd.to_numeric(sample["close"], errors="coerce")
        sample = (
            sample.dropna(subset=["price_time", "close"])
            .drop_duplicates("price_time", keep="last")
            .sort_values("price_time")
        )
        if len(sample) < min_history:
            continue
        close = sample.set_index("price_time")["close"]
        indicators = short_term_indicator_frame(close)
        if indicators.empty:
            continue
        latest = indicators.dropna(subset=["close"]).iloc[-1]
        metadata = getattr(asset, "metadata_json", {}) or {}
        price_bases = (
            sorted(sample["price_basis"].dropna().astype(str).unique().tolist())
            if "price_basis" in sample
            else []
        )
        rows.append({
            "symbol": asset.symbol,
            "name": asset.name,
            "asset_type": asset.asset_type,
            "sector": metadata.get("sector_33") or metadata.get("sector_17") or "未分類",
            "observations": len(sample),
            "data_as_of": sample["price_time"].max(),
            "price_basis": ", ".join(price_bases) if price_bases else "unknown",
            "latest_close": float(latest["close"]),
            "return_20d": latest.get("return_20d"),
            "volatility_20d": latest.get("volatility_20d"),
            "rsi_14": latest.get("rsi_14"),
        })
    return pd.DataFrame(rows, columns=columns).sort_values(
        "return_20d", ascending=False, na_position="last"
    )
