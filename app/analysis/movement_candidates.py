import pandas as pd

from app.analysis.correlation import close_wide, horizon_correlations, us_japan_pair_frame
from app.analysis.market_calendar import latest_contiguous_exchange_observations
from app.analysis.technical import short_term_indicator_frame


US_INDEX_SYMBOLS = ["NASDAQCOM", "DJIA", "SP500"]
JAPAN_INDEX_SYMBOL = "NIKKEI225"


def latest_us_market_signal(wide: pd.DataFrame, us_symbols: list[str] | None = None) -> dict:
    us_symbols = us_symbols or US_INDEX_SYMBOLS
    available = [symbol for symbol in us_symbols if symbol in wide.columns]
    if wide.empty or not available:
        return {"direction": "不明", "average_return": pd.NA, "reasons": ["米国指数データが不足しています"]}

    returns = wide[available].pct_change(fill_method=None).dropna(how="all")
    if returns.empty:
        return {"direction": "不明", "average_return": pd.NA, "reasons": ["米国指数の騰落率を計算できません"]}

    latest = returns.tail(1).iloc[0]
    average_return = latest.mean()
    if average_return >= 0.01:
        direction = "強い上昇"
    elif average_return >= 0.003:
        direction = "上昇"
    elif average_return <= -0.01:
        direction = "強い下落"
    elif average_return <= -0.003:
        direction = "下落"
    else:
        direction = "小動き"

    reasons = [f"{symbol}: {value:.2%}" for symbol, value in latest.dropna().items()]
    return {"direction": direction, "average_return": float(average_return), "reasons": reasons}


def us_japan_market_context(index_prices: pd.DataFrame) -> dict:
    wide = close_wide(index_prices)
    pair_summaries = []
    correlations = []
    for us_symbol in US_INDEX_SYMBOLS:
        pair = us_japan_pair_frame(wide, us_symbol, JAPAN_INDEX_SYMBOL, calendar_aware=True)
        if pair.empty:
            continue
        horizons = horizon_correlations(pair, [20, 60])
        for row in horizons.to_dict(orient="records"):
            correlation = row["correlation"]
            if pd.notna(correlation):
                correlations.append(float(correlation))
            pair_summaries.append(
                {
                    "us_symbol": us_symbol,
                    "japan_symbol": JAPAN_INDEX_SYMBOL,
                    "window_days": int(row["window_days"]),
                    "correlation": None if pd.isna(correlation) else float(correlation),
                    "sample_size": int(row["sample_size"]),
                }
            )
    average_correlation = sum(correlations) / len(correlations) if correlations else pd.NA
    return {
        "wide": wide,
        "us_signal": latest_us_market_signal(wide),
        "average_correlation": average_correlation,
        "pair_summaries": pair_summaries,
    }


def build_movement_candidates(
    index_prices: pd.DataFrame,
    japan_prices: pd.DataFrame,
    min_observations: int = 30,
    limit: int = 20,
    feedback_by_symbol: dict[str, dict] | None = None,
) -> dict:
    context = us_japan_market_context(index_prices)
    if japan_prices.empty:
        return {**context, "candidates": pd.DataFrame(), "insufficient": pd.DataFrame()}

    rows = []
    insufficient = []
    frame = japan_prices.copy()
    frame["price_time"] = pd.to_datetime(frame["price_time"], utc=True).dt.normalize()
    frame["close"] = pd.to_numeric(frame["close"])
    for symbol, group in frame.groupby("symbol"):
        ordered = group.drop_duplicates("price_time").set_index("price_time").sort_index()
        total_observations = len(ordered)
        contiguous_observations = latest_contiguous_exchange_observations(
            ordered.index, "XTKS"
        )
        ordered = ordered.tail(contiguous_observations)
        close = ordered["close"]
        name = group["name"].dropna().iloc[-1] if "name" in group and not group["name"].dropna().empty else symbol
        if contiguous_observations < min_observations:
            insufficient.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "observations": contiguous_observations,
                    "total_observations": total_observations,
                    "reason": "latest_contiguous_sessions_below_minimum",
                }
            )
            continue

        indicators = short_term_indicator_frame(close)
        latest = indicators.dropna(subset=["close"]).tail(1)
        if latest.empty:
            insufficient.append({"symbol": symbol, "name": name, "observations": len(close)})
            continue
        row = latest.iloc[0]
        score, direction, reasons = movement_score(row, context["average_correlation"], context["us_signal"])
        feedback = (feedback_by_symbol or {}).get(symbol)
        score, feedback_reason, feedback_score = apply_virtual_trade_feedback(score, feedback)
        if feedback_reason:
            reasons.append(feedback_reason)
        rows.append(
            {
                "symbol": symbol,
                "name": name,
                "score": score,
                "direction": direction,
                "latest_close": float(row["close"]),
                "return_1d": none_if_na(row.get("return_1d")),
                "return_5d": none_if_na(row.get("return_5d")),
                "return_20d": none_if_na(row.get("return_20d")),
                "volatility_20d": none_if_na(row.get("volatility_20d")),
                "rsi_14": none_if_na(row.get("rsi_14")),
                "feedback_score": feedback_score,
                "reasons": reasons,
                "observations": len(close),
            }
        )

    candidates = pd.DataFrame(rows)
    eligible_count = len(candidates)
    if not candidates.empty:
        candidates = candidates.sort_values(["score", "volatility_20d"], ascending=[False, False]).head(limit)
    return {
        **context,
        "candidates": candidates,
        "eligible_count": eligible_count,
        "insufficient": pd.DataFrame(insufficient),
    }


def movement_score(row: pd.Series, average_correlation, us_signal: dict) -> tuple[int, str, list[str]]:
    score = 40
    reasons: list[str] = []
    direction = "方向感は未確定"

    corr = 0 if pd.isna(average_correlation) else float(average_correlation)
    if abs(corr) >= 0.45:
        score += 15
        reasons.append(f"米国指数と日経平均の相関が比較的高いです（平均 {corr:.2f}）")
    elif abs(corr) >= 0.25:
        score += 8
        reasons.append(f"米国指数と日経平均の相関があります（平均 {corr:.2f}）")
    else:
        reasons.append("米国指数と日経平均の相関は強くありません")

    us_return = us_signal.get("average_return")
    if pd.notna(us_return):
        if abs(float(us_return)) >= 0.01:
            score += 10
            reasons.append(f"直近の米国指数平均が大きく動いています（{float(us_return):.2%}）")
        elif abs(float(us_return)) >= 0.003:
            score += 5
            reasons.append(f"直近の米国指数平均に方向感があります（{float(us_return):.2%}）")
        if corr >= 0 and float(us_return) > 0:
            direction = "上方向に動きやすい可能性"
        elif corr >= 0 and float(us_return) < 0:
            direction = "下方向に動きやすい可能性"

    volatility = row.get("volatility_20d")
    if pd.notna(volatility):
        if volatility >= 0.035:
            score += 18
            reasons.append(f"20日ボラティリティが高めです（{volatility:.2%}）")
        elif volatility >= 0.02:
            score += 10
            reasons.append(f"20日ボラティリティがあります（{volatility:.2%}）")

    return_5d = row.get("return_5d")
    if pd.notna(return_5d):
        if abs(return_5d) >= 0.05:
            score += 12
            reasons.append(f"5日騰落率が大きいです（{return_5d:.2%}）")
        elif abs(return_5d) >= 0.025:
            score += 6
            reasons.append(f"5日騰落率に動きがあります（{return_5d:.2%}）")

    rsi_value = row.get("rsi_14")
    if pd.notna(rsi_value) and (rsi_value >= 70 or rsi_value <= 30):
        score += 8
        reasons.append(f"RSIが極端な水準です（{rsi_value:.1f}）")

    close = row.get("close")
    upper = row.get("bb_upper")
    lower = row.get("bb_lower")
    if pd.notna(close) and pd.notna(upper) and pd.notna(lower):
        if close >= upper:
            score += 8
            reasons.append("終値がボリンジャーバンド上限付近です")
        elif close <= lower:
            score += 8
            reasons.append("終値がボリンジャーバンド下限付近です")

    return min(100, max(0, int(round(score)))), direction, reasons[:5]


def none_if_na(value):
    return None if pd.isna(value) else float(value)


def apply_virtual_trade_feedback(score: int, feedback: dict | None) -> tuple[int, str | None, float | None]:
    """Return virtual results as an uncalibrated note without changing score."""
    if not feedback:
        return score, None, None

    trades = int(feedback.get("trades", 0))
    win_rate = feedback.get("win_rate", 0)
    average_return = feedback.get("average_return", 0)
    large_move_rate = feedback.get("large_move_rate", 0)
    reason = (
        f"仮想投資の参考統計（スコア未反映・{trades}件）: 勝率 {win_rate:.0%}, "
        f"平均損益 {average_return:.2%}, 大幅変動率 {large_move_rate:.0%}"
    )
    return score, reason, None
