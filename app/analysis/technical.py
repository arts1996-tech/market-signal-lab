from functools import lru_cache

import pandas as pd

from app.analysis.market_calendar import consecutive_weekday_returns, exchange_calendar


PERIOD_FREQUENCIES = {"weekly": "W-FRI", "monthly": "M"}


def _normalized_series(values: pd.Series) -> pd.Series:
    normalized = pd.to_numeric(values, errors="coerce").dropna().sort_index()
    normalized.index = pd.to_datetime(normalized.index, utc=True).normalize()
    return normalized[~normalized.index.duplicated(keep="last")]


@lru_cache(maxsize=256)
def _exchange_sessions(
    calendar_name: str,
    start_date: str,
    end_date: str,
) -> tuple[pd.Timestamp, ...]:
    try:
        sessions = exchange_calendar(calendar_name).sessions_in_range(
            pd.Timestamp(start_date), pd.Timestamp(end_date)
        )
    except ValueError:
        return ()
    return tuple(pd.Timestamp(value).tz_localize(None).normalize() for value in sessions)


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


def completed_period_returns(
    close: pd.Series,
    period: str,
    *,
    calendar_name: str = "XTKS",
) -> pd.Series:
    """Close-to-close returns for completed exchange weeks or months only."""

    if period not in PERIOD_FREQUENCIES:
        raise ValueError("period must be 'weekly' or 'monthly'")
    ordered = _normalized_series(close)
    if ordered.empty:
        return pd.Series(dtype=float)
    frequency = PERIOD_FREQUENCIES[period]
    naive_index = ordered.index.tz_localize(None)
    observed_periods = naive_index.to_period(frequency)
    first_period = observed_periods.min()
    last_period = observed_periods.max()
    sessions = pd.DatetimeIndex(
        _exchange_sessions(
            calendar_name,
            first_period.start_time.date().isoformat(),
            last_period.end_time.date().isoformat(),
        )
    )
    if sessions.empty:
        return pd.Series(dtype=float)
    session_periods = sessions.to_period(frequency)
    expected_ends = pd.Series(sessions, index=session_periods).groupby(level=0).max()
    endpoints: dict[pd.Timestamp, float] = {}
    for period_value in observed_periods.unique():
        if period_value not in expected_ends.index:
            continue
        positions = observed_periods == period_value
        period_values = ordered.iloc[positions]
        observed_end = period_values.index[-1].tz_localize(None)
        if observed_end == expected_ends.loc[period_value]:
            endpoints[period_values.index[-1]] = float(period_values.iloc[-1])
    endpoint_series = pd.Series(endpoints, dtype=float).sort_index()
    return endpoint_series.pct_change(fill_method=None).dropna()


def distance_from_rolling_high(
    close: pd.Series,
    high: pd.Series | None = None,
    window: int = 252,
) -> float | None:
    """Return latest-close distance from a fully observed rolling price high."""

    ordered = _normalized_series(close)
    if len(ordered) < window:
        return None
    latest_close = ordered.tail(window)
    if high is None:
        high_window = latest_close
    else:
        normalized_high = _normalized_series(high).reindex(latest_close.index)
        if normalized_high.isna().any() or len(normalized_high) < window:
            return None
        valid = (normalized_high >= latest_close) & (normalized_high > 0)
        if not valid.all():
            return None
        high_window = normalized_high
    rolling_high = float(high_window.max())
    if rolling_high <= 0:
        return None
    return float(latest_close.iloc[-1] / rolling_high - 1)


def horizon_relative_strength(
    asset_close: pd.Series,
    benchmark_close: pd.Series,
    *,
    horizon: int = 20,
    calendar_name: str = "XTKS",
) -> dict[str, float | int | None]:
    """Compare exact endpoints after requiring the same complete session window."""

    asset = _normalized_series(asset_close)
    benchmark = _normalized_series(benchmark_close)
    empty = {
        "asset_return": None,
        "benchmark_return": None,
        "relative_strength": None,
        "sessions": 0,
    }
    if horizon < 1 or len(asset) < horizon + 1 or benchmark.empty:
        return empty
    asset_window = asset.tail(horizon + 1)
    start = asset_window.index[0].tz_localize(None)
    end = asset_window.index[-1].tz_localize(None)
    expected = _exchange_sessions(
        calendar_name, start.date().isoformat(), end.date().isoformat()
    )
    expected_index = pd.DatetimeIndex(expected)
    asset_dates = asset_window.index.tz_localize(None)
    benchmark_by_date = benchmark.copy()
    benchmark_by_date.index = benchmark_by_date.index.tz_localize(None)
    if (
        len(expected_index) != horizon + 1
        or not asset_dates.equals(expected_index)
        or not expected_index.isin(benchmark_by_date.index).all()
    ):
        return empty
    benchmark_window = benchmark_by_date.reindex(expected_index)
    if benchmark_window.isna().any():
        return empty
    asset_start = float(asset_window.iloc[0])
    benchmark_start = float(benchmark_window.iloc[0])
    if asset_start <= 0 or benchmark_start <= 0:
        return empty
    asset_return = float(asset_window.iloc[-1] / asset_start - 1)
    benchmark_return = float(benchmark_window.iloc[-1] / benchmark_start - 1)
    return {
        "asset_return": asset_return,
        "benchmark_return": benchmark_return,
        "relative_strength": asset_return - benchmark_return,
        "sessions": horizon,
    }


def short_term_indicator_frame(
    close: pd.Series,
    high: pd.Series | None = None,
    low: pd.Series | None = None,
) -> pd.DataFrame:
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
    if high is not None and low is not None:
        aligned_high = pd.to_numeric(high, errors="coerce").reindex(ordered.index)
        aligned_low = pd.to_numeric(low, errors="coerce").reindex(ordered.index)
        valid = (
            aligned_high.notna()
            & aligned_low.notna()
            & (aligned_high >= aligned_low)
            & (aligned_high >= ordered)
            & (aligned_low <= ordered)
        )
        aligned_high = aligned_high.where(valid)
        aligned_low = aligned_low.where(valid)
        frame["atr_14"] = atr(aligned_high, aligned_low, ordered, 14)
        frame["atr_pct_14"] = frame["atr_14"] / ordered.where(ordered > 0)
    else:
        frame["atr_14"] = pd.NA
        frame["atr_pct_14"] = pd.NA
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
