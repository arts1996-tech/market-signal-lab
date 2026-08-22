"""Deterministic stock/ETF screening primitives for phase 3."""

import pandas as pd

from app.analysis.market_calendar import latest_contiguous_exchange_observations
from app.analysis.technical import (
    completed_period_returns,
    distance_from_rolling_high,
    horizon_relative_strength,
    short_term_indicator_frame,
)


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
    benchmark_prices: pd.DataFrame | None = None,
    benchmark_symbol: str = "NIKKEI225",
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
        "weekly_return",
        "weekly_period_end",
        "monthly_return",
        "monthly_period_end",
        "return_20d",
        "volatility_20d",
        "rsi_14",
        "atr_14",
        "atr_pct_14",
        "benchmark_symbol",
        "benchmark_return_20d",
        "relative_strength_vs_benchmark_20d",
        "sector_peer_return_20d",
        "relative_strength_vs_sector_20d",
        "sector_peer_count",
        "distance_from_52week_high",
        "metric_quality_reasons",
    ]
    if prices.empty or assets.empty:
        return pd.DataFrame(columns=columns)
    benchmark_close = pd.Series(dtype=float)
    if benchmark_prices is not None and not benchmark_prices.empty:
        benchmark_frame = benchmark_prices.copy()
        if "symbol" in benchmark_frame:
            benchmark_frame = benchmark_frame[
                benchmark_frame["symbol"] == benchmark_symbol
            ]
        benchmark_frame["price_time"] = pd.to_datetime(
            benchmark_frame["price_time"], utc=True, errors="coerce"
        ).dt.normalize()
        benchmark_frame["close"] = pd.to_numeric(
            benchmark_frame["close"], errors="coerce"
        )
        benchmark_close = (
            benchmark_frame.dropna(subset=["price_time", "close"])
            .drop_duplicates("price_time", keep="last")
            .set_index("price_time")["close"]
            .sort_index()
        )
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
        indexed = sample.set_index("price_time")
        close = indexed["close"]
        high = indexed["high"] if "high" in indexed else None
        low = indexed["low"] if "low" in indexed else None
        indicators = short_term_indicator_frame(close, high=high, low=low)
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
        metric_quality_reasons: list[str] = []
        weekly = completed_period_returns(close, "weekly")
        monthly = completed_period_returns(close, "monthly")
        benchmark = horizon_relative_strength(close, benchmark_close, horizon=20)
        distance_52week = distance_from_rolling_high(close, high=high, window=252)
        if weekly.empty:
            metric_quality_reasons.append("weekly_return_insufficient_completed_periods")
            attention["quality_warnings"].append("週次リターンに必要な完了週が不足")
        if monthly.empty:
            metric_quality_reasons.append("monthly_return_insufficient_completed_periods")
            attention["quality_warnings"].append("月次リターンに必要な完了月が不足")
        if pd.isna(latest.get("atr_14")):
            metric_quality_reasons.append("atr_unavailable_missing_valid_ohlc")
            attention["quality_warnings"].append("ATRに必要な有効な高値・安値が不足")
        if benchmark["relative_strength"] is None:
            metric_quality_reasons.append("benchmark_relative_strength_unavailable")
            attention["quality_warnings"].append("日経平均と同じ20営業日の比較データが不足")
        if distance_52week is None:
            metric_quality_reasons.append("distance_52week_insufficient_history")
            attention["quality_warnings"].append("52週高値乖離に必要な252営業日が不足")
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
            "weekly_return": None if weekly.empty else float(weekly.iloc[-1]),
            "weekly_period_end": None if weekly.empty else weekly.index[-1],
            "monthly_return": None if monthly.empty else float(monthly.iloc[-1]),
            "monthly_period_end": None if monthly.empty else monthly.index[-1],
            "return_20d": latest.get("return_20d"),
            "volatility_20d": latest.get("volatility_20d"),
            "rsi_14": latest.get("rsi_14"),
            "atr_14": latest.get("atr_14"),
            "atr_pct_14": latest.get("atr_pct_14"),
            "benchmark_symbol": benchmark_symbol,
            "benchmark_return_20d": benchmark["benchmark_return"],
            "relative_strength_vs_benchmark_20d": benchmark["relative_strength"],
            "sector_peer_return_20d": None,
            "relative_strength_vs_sector_20d": None,
            "sector_peer_count": 0,
            "distance_from_52week_high": distance_52week,
            "metric_quality_reasons": metric_quality_reasons,
        })
    result = pd.DataFrame(rows, columns=columns)
    if not result.empty:
        grouped = result.groupby(["sector", "data_as_of"], dropna=False)["return_20d"]
        group_count = grouped.transform("count")
        group_sum = grouped.transform("sum")
        valid_sector = result["return_20d"].notna() & (group_count >= 3)
        result.loc[valid_sector, "sector_peer_count"] = (
            group_count[valid_sector] - 1
        ).astype(int)
        result.loc[valid_sector, "sector_peer_return_20d"] = (
            group_sum[valid_sector] - result.loc[valid_sector, "return_20d"]
        ) / (group_count[valid_sector] - 1)
        result.loc[valid_sector, "relative_strength_vs_sector_20d"] = (
            result.loc[valid_sector, "return_20d"]
            - result.loc[valid_sector, "sector_peer_return_20d"]
        )
        for index in result.index[~valid_sector]:
            result.at[index, "metric_quality_reasons"].append(
                "sector_relative_strength_insufficient_peers"
            )
            result.at[index, "quality_warnings"].append(
                "同じ分析日の同業種比較に必要な他銘柄が不足"
            )
    return result.sort_values(
        ["attention_score", "return_20d"],
        ascending=[False, False],
        na_position="last",
    )
