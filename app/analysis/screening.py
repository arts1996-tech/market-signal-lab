"""Deterministic stock/ETF screening primitives for phase 3."""

import pandas as pd

from app.analysis.market_calendar import latest_contiguous_exchange_observations
from app.analysis.technical import short_term_indicator_frame


SCREENING_MIN_HISTORY = 30


def technical_attention_snapshot(latest: pd.Series, observations: int) -> dict:
    """Score unusual technical conditions without producing a buy/sell judgment."""
    score = 30
    reasons: list[str] = []
    warnings: list[str] = []

    return_20d = latest.get("return_20d")
    if pd.notna(return_20d):
        absolute_return = abs(float(return_20d))
        if absolute_return >= 0.10:
            score += 25
        elif absolute_return >= 0.05:
            score += 15
        elif absolute_return >= 0.02:
            score += 8
        if absolute_return >= 0.02:
            reasons.append(f"20日騰落率が{float(return_20d):+.2%}です")

    volatility_20d = latest.get("volatility_20d")
    if pd.notna(volatility_20d):
        volatility = float(volatility_20d)
        if volatility >= 0.03:
            score += 20
        elif volatility >= 0.02:
            score += 12
        elif volatility >= 0.01:
            score += 5
        if volatility >= 0.02:
            reasons.append(f"20日ボラティリティが{volatility:.2%}です")

    rsi_14 = latest.get("rsi_14")
    if pd.notna(rsi_14):
        rsi_value = float(rsi_14)
        if rsi_value >= 70 or rsi_value <= 30:
            score += 15
            reasons.append(f"RSIが極端な水準です（{rsi_value:.1f}）")
        elif rsi_value >= 60 or rsi_value <= 40:
            score += 8
            reasons.append(f"RSIに偏りがあります（{rsi_value:.1f}）")

    close = latest.get("close")
    upper = latest.get("bb_upper")
    lower = latest.get("bb_lower")
    if pd.notna(close) and pd.notna(upper) and pd.notna(lower):
        if float(close) >= float(upper):
            score += 10
            reasons.append("終値がボリンジャーバンド上限以上です")
        elif float(close) <= float(lower):
            score += 10
            reasons.append("終値がボリンジャーバンド下限以下です")

    histogram = latest.get("histogram")
    if pd.notna(histogram) and pd.notna(close) and float(close) != 0:
        macd_ratio = abs(float(histogram) / float(close))
        if macd_ratio >= 0.01:
            score += 10
            reasons.append("MACDヒストグラムの振れが大きめです")
        elif macd_ratio >= 0.005:
            score += 5

    missing = [
        name
        for name, value in {
            "20日騰落率": return_20d,
            "20日ボラティリティ": volatility_20d,
            "RSI": rsi_14,
        }.items()
        if pd.isna(value)
    ]
    if missing:
        warnings.append(f"未算出指標: {', '.join(missing)}")
    if observations < 50:
        warnings.append("50日・75日移動平均は未算出")
    elif observations < 75:
        warnings.append("75日移動平均は未算出")

    bounded_score = min(100, max(0, int(round(score))))
    if bounded_score >= 70:
        label = "高い注目度"
    elif bounded_score >= 50:
        label = "要確認"
    else:
        label = "通常"
    return {
        "attention_score": bounded_score,
        "attention_label": label,
        "attention_reasons": reasons[:4] or ["短期指標に大きな偏りはありません"],
        "quality_warnings": warnings,
    }


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
        "attention_score",
        "attention_label",
        "attention_reasons",
        "quality_warnings",
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
        total_observations = len(sample)
        contiguous_observations = latest_contiguous_exchange_observations(
            sample["price_time"], "XTKS"
        )
        if contiguous_observations < min_history:
            continue
        sample = sample.tail(contiguous_observations)
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
        attention = technical_attention_snapshot(latest, len(sample))
        if total_observations > contiguous_observations:
            attention["quality_warnings"].append(
                f"非連続の過去観測{total_observations - contiguous_observations}件を除外"
            )
        rows.append({
            "symbol": asset.symbol,
            "name": asset.name,
            "asset_type": asset.asset_type,
            "sector": metadata.get("sector_33") or metadata.get("sector_17") or "未分類",
            "observations": len(sample),
            "data_as_of": sample["price_time"].max(),
            "price_basis": ", ".join(price_bases) if price_bases else "unknown",
            **attention,
            "latest_close": float(latest["close"]),
            "return_20d": latest.get("return_20d"),
            "volatility_20d": latest.get("volatility_20d"),
            "rsi_14": latest.get("rsi_14"),
        })
    return pd.DataFrame(rows, columns=columns).sort_values(
        ["attention_score", "return_20d"],
        ascending=[False, False],
        na_position="last",
    )
