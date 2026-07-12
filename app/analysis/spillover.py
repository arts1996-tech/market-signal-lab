"""US previous-session to Japan current-session spillover features.

The module intentionally uses only observed values.  In particular, the US
series is a close-only series from FRED and the Japan series is J-Quants OHLC.
No missing open, high, low, or volume value is estimated.
"""

import pandas as pd

from app.analysis.market_calendar import consecutive_weekday_returns, is_next_exchange_session, is_next_weekday, next_exchange_session


TARGET_METRICS = ("gap_return", "intraday_return", "daily_return")


def japan_session_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Calculate observed gap, intraday and daily returns for a Japan asset."""
    required = {"price_time", "open", "close"}
    if prices.empty or not required.issubset(prices.columns):
        return pd.DataFrame(columns=["japan_date", *TARGET_METRICS])

    columns = ["price_time", "open", "close", "adjusted_open", "adjusted_close", "adjustment_factor"]
    frame = prices[[column for column in columns if column in prices]].copy()
    frame["price_time"] = pd.to_datetime(frame["price_time"], utc=True).dt.normalize()
    frame["open"] = pd.to_numeric(frame["open"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    has_adjusted_ohlc = {"adjusted_open", "adjusted_close"}.issubset(frame.columns)
    if has_adjusted_ohlc:
        frame["adjusted_open"] = pd.to_numeric(frame["adjusted_open"], errors="coerce")
        frame["adjusted_close"] = pd.to_numeric(frame["adjusted_close"], errors="coerce")
        frame["analysis_open"] = frame["adjusted_open"].where(frame["adjusted_open"].notna(), frame["open"])
        frame["analysis_close"] = frame["adjusted_close"].where(frame["adjusted_close"].notna(), frame["close"])
        frame["price_basis"] = frame["adjusted_open"].notna() & frame["adjusted_close"].notna()
    else:
        frame["analysis_open"] = frame["open"]
        frame["analysis_close"] = frame["close"]
        frame["price_basis"] = False
    frame = frame.sort_values("price_time").drop_duplicates("price_time", keep="last")
    frame["previous_session_date"] = frame["price_time"].shift(1)
    frame["previous_close"] = frame["analysis_close"].shift(1)
    frame["is_consecutive_weekday"] = [
        False,
        *[
            is_next_weekday(previous, current)
            for previous, current in zip(
                frame["price_time"].iloc[:-1], frame["price_time"].iloc[1:], strict=True
            )
        ],
    ]
    frame.loc[~frame["is_consecutive_weekday"], "previous_close"] = pd.NA
    if "adjustment_factor" in frame:
        frame["adjustment_factor"] = pd.to_numeric(frame["adjustment_factor"], errors="coerce")
        factor_changed = frame["adjustment_factor"].ne(frame["adjustment_factor"].shift(1))
        frame.loc[factor_changed & ~frame["price_basis"], "previous_close"] = pd.NA
    frame["gap_return"] = frame["analysis_open"] / frame["previous_close"] - 1
    frame["intraday_return"] = frame["analysis_close"] / frame["analysis_open"] - 1
    frame["daily_return"] = frame["analysis_close"] / frame["previous_close"] - 1
    return frame.rename(columns={"price_time": "japan_date"})[
        ["japan_date", "previous_session_date", "is_consecutive_weekday", "price_basis", *TARGET_METRICS]
    ]


def us_japan_spillover_frame(
    us_close: pd.Series, japan_prices: pd.DataFrame, calendar_aware: bool = False
) -> pd.DataFrame:
    """Map a Japan session to the latest strictly earlier observed US session."""
    japan = japan_session_returns(japan_prices)
    if japan.empty or us_close.empty:
        return pd.DataFrame(columns=["japan_date", "us_date", "us_return", *TARGET_METRICS])

    us = pd.to_numeric(us_close, errors="coerce").dropna().sort_index()
    us.index = pd.to_datetime(us.index, utc=True).normalize()
    us = us[~us.index.duplicated(keep="last")]
    us_returns = consecutive_weekday_returns(us)
    rows: list[dict] = []
    us_dates = list(us_returns.index)
    cursor = 0
    previous_japan_date = None
    for japan_row in japan.itertuples(index=False):
        japan_date = japan_row.japan_date
        while cursor < len(us_dates) and us_dates[cursor] < japan_date:
            cursor += 1
        if cursor == 0:
            continue
        us_date = us_dates[cursor - 1]
        if calendar_aware and next_exchange_session(us_date, "XTKS") != pd.Timestamp(japan_date).tz_convert(None).normalize():
            continue
        if calendar_aware:
            component_dates = [
                candidate for candidate in us_dates
                if candidate <= us_date
                and (previous_japan_date is None or pd.Timestamp(candidate).tz_convert(None).normalize() >= pd.Timestamp(previous_japan_date).tz_convert(None).normalize())
            ]
            aligned_us_return = (1 + us_returns.loc[component_dates]).prod() - 1
        else:
            aligned_us_return = us_returns.loc[us_date]
        rows.append(
            {
                "japan_date": japan_date,
                "us_date": us_date,
                "us_return": float(aligned_us_return),
                "gap_return": japan_row.gap_return,
                "intraday_return": japan_row.intraday_return,
                "daily_return": japan_row.daily_return,
            }
        )
        previous_japan_date = japan_date
    return pd.DataFrame(rows, columns=["japan_date", "us_date", "us_return", *TARGET_METRICS])


def spillover_conditional_stats(frame: pd.DataFrame, target_metric: str) -> pd.DataFrame:
    """Summarize observed Japan returns by preceding US return bucket."""
    columns = ["us_return_condition", "sample_size", "mean_return", "median_return", "positive_rate"]
    if frame.empty or target_metric not in TARGET_METRICS:
        return pd.DataFrame(columns=columns)
    data = frame[["us_return", target_metric]].dropna().copy()
    if data.empty:
        return pd.DataFrame(columns=columns)
    bins = [-float("inf"), -0.02, -0.01, -0.005, 0.005, 0.01, 0.02, float("inf")]
    labels = ["≤ -2%", "-2% to -1%", "-1% to -0.5%", "-0.5% to 0.5%", "0.5% to 1%", "1% to 2%", "≥ 2%"]
    data["us_return_condition"] = pd.cut(data["us_return"], bins=bins, labels=labels, include_lowest=True)
    summary = (
        data.groupby("us_return_condition", observed=False)[target_metric]
        .agg(sample_size="count", mean_return="mean", median_return="median")
        .reset_index()
    )
    positive_rate = (
        data.assign(positive=data[target_metric] > 0)
        .groupby("us_return_condition", observed=False)["positive"]
        .mean()
        .reset_index(name="positive_rate")
    )
    return summary.merge(positive_rate, on="us_return_condition")
