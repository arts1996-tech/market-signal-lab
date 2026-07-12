import pandas as pd

from app.analysis.market_calendar import consecutive_weekday_returns


def daily_returns(close: pd.Series) -> pd.Series:
    return consecutive_weekday_returns(close)


def simple_moving_average(close: pd.Series, window: int) -> pd.Series:
    return close.sort_index().rolling(window=window, min_periods=window).mean()


def exponential_moving_average(close: pd.Series, span: int) -> pd.Series:
    return close.sort_index().ewm(span=span, adjust=False, min_periods=span).mean()


def bollinger_bands(close: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    ordered = close.sort_index()
    middle = simple_moving_average(ordered, window)
    std = ordered.rolling(window=window, min_periods=window).std()
    return pd.DataFrame(
        {
            "bb_upper": middle + num_std * std,
            "bb_middle": middle,
            "bb_lower": middle - num_std * std,
        }
    )


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    diff = close.sort_index().diff()
    gain = diff.clip(lower=0)
    loss = -diff.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(close: pd.Series) -> pd.DataFrame:
    ordered = close.sort_index()
    ema12 = ordered.ewm(span=12, adjust=False).mean()
    ema26 = ordered.ewm(span=26, adjust=False).mean()
    line = ema12 - ema26
    signal = line.ewm(span=9, adjust=False).mean()
    return pd.DataFrame({"macd": line, "signal": signal, "histogram": line - signal})


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    ordered = pd.DataFrame({"high": high, "low": low, "close": close}).sort_index()
    previous_close = ordered["close"].shift(1)
    true_range = pd.concat(
        [
            ordered["high"] - ordered["low"],
            (ordered["high"] - previous_close).abs(),
            (ordered["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(window=window, min_periods=window).mean()


def short_term_indicator_frame(close: pd.Series) -> pd.DataFrame:
    ordered = close.sort_index()
    frame = pd.DataFrame({"close": ordered})
    for window in [5, 20, 25, 50, 75]:
        frame[f"sma_{window}"] = simple_moving_average(ordered, window)
    frame["ema_12"] = exponential_moving_average(ordered, 12)
    frame["ema_26"] = exponential_moving_average(ordered, 26)
    frame["rsi_14"] = rsi(ordered, 14)
    frame = frame.join(macd(ordered))
    frame = frame.join(bollinger_bands(ordered, 20, 2.0))
    frame["return_1d"] = consecutive_weekday_returns(ordered).reindex(ordered.index)
    frame["return_5d"] = ordered.pct_change(5, fill_method=None)
    frame["return_20d"] = ordered.pct_change(20, fill_method=None)
    frame["volatility_20d"] = frame["return_1d"].rolling(20, min_periods=20).std()
    frame["drawdown"] = ordered / ordered.cummax() - 1
    return frame


def short_term_signal_snapshot(indicators: pd.DataFrame) -> dict:
    if "close" not in indicators.columns:
        return {
            "score": 50,
            "label": "データ不足",
            "positive_reasons": [],
            "negative_reasons": ["価格データが不足しています"],
        }

    latest = indicators.dropna(subset=["close"]).tail(1)
    if latest.empty:
        return {
            "score": 50,
            "label": "中立",
            "positive_reasons": [],
            "negative_reasons": ["価格データが不足しています"],
        }

    row = latest.iloc[0]
    score = 50
    positive: list[str] = []
    negative: list[str] = []

    if pd.notna(row.get("sma_20")) and row["close"] > row["sma_20"]:
        score += 12
        positive.append("終値が20日移動平均を上回っています")
    elif pd.notna(row.get("sma_20")):
        score -= 12
        negative.append("終値が20日移動平均を下回っています")

    if pd.notna(row.get("sma_50")) and row["close"] > row["sma_50"]:
        score += 8
        positive.append("終値が50日移動平均を上回っています")
    elif pd.notna(row.get("sma_50")):
        score -= 8
        negative.append("終値が50日移動平均を下回っています")

    if pd.notna(row.get("macd")) and pd.notna(row.get("signal")) and row["macd"] > row["signal"]:
        score += 10
        positive.append("MACDがシグナルを上回っています")
    elif pd.notna(row.get("macd")) and pd.notna(row.get("signal")):
        score -= 10
        negative.append("MACDがシグナルを下回っています")

    if pd.notna(row.get("rsi_14")):
        if row["rsi_14"] >= 75:
            score -= 10
            negative.append("RSIが高く、短期的な過熱感があります")
        elif row["rsi_14"] <= 30:
            score += 5
            positive.append("RSIが低く、短期的な反発余地があります")
        elif 45 <= row["rsi_14"] <= 65:
            score += 5
            positive.append("RSIは過熱しすぎていない範囲です")

    if pd.notna(row.get("return_20d")) and row["return_20d"] > 0:
        score += 5
        positive.append("20営業日リターンがプラスです")
    elif pd.notna(row.get("return_20d")):
        score -= 5
        negative.append("20営業日リターンがマイナスです")

    score = max(0, min(100, score))
    if score >= 70:
        label = "やや強気"
    elif score >= 55:
        label = "中立からやや強気"
    elif score >= 45:
        label = "中立"
    elif score >= 30:
        label = "やや慎重"
    else:
        label = "慎重"

    return {
        "score": score,
        "label": label,
        "positive_reasons": positive,
        "negative_reasons": negative,
    }
