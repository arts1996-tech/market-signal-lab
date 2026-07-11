import pandas as pd

from app.analysis.market_calendar import align_us_previous_to_japan
from app.analysis.technical import daily_returns


WINDOWS = [20, 60, 120, 250]


def close_wide(prices: pd.DataFrame) -> pd.DataFrame:
    if prices.empty:
        return pd.DataFrame()
    frame = prices.copy()
    frame["price_time"] = pd.to_datetime(frame["price_time"], utc=True).dt.normalize()
    frame["close"] = pd.to_numeric(frame["close"])
    return frame.pivot_table(index="price_time", columns="symbol", values="close", aggfunc="last").sort_index()


def normalized_index(wide: pd.DataFrame) -> pd.DataFrame:
    if wide.empty:
        return wide
    first_valid = wide.apply(lambda s: s.dropna().iloc[0] if not s.dropna().empty else pd.NA)
    return wide.divide(first_valid).multiply(100)


def return_wide(wide: pd.DataFrame) -> pd.DataFrame:
    return wide.apply(daily_returns).dropna(how="all")


def us_japan_pair_frame(wide: pd.DataFrame, us_symbol: str, japan_symbol: str) -> pd.DataFrame:
    returns = return_wide(wide)
    if us_symbol not in returns or japan_symbol not in returns:
        return pd.DataFrame()
    return align_us_previous_to_japan(returns[us_symbol], returns[japan_symbol])


def rolling_correlation(pair_frame: pd.DataFrame, window: int = 60) -> pd.Series:
    if pair_frame.empty:
        return pd.Series(dtype="float64")
    indexed = pair_frame.set_index("japan_date")
    return indexed["us_return"].rolling(window).corr(indexed["japan_return"]).dropna()


def horizon_correlations(pair_frame: pd.DataFrame, windows: list[int] | None = None) -> pd.DataFrame:
    windows = windows or WINDOWS
    rows = []
    for window in windows:
        sample = pair_frame.tail(window)
        corr = sample["us_return"].corr(sample["japan_return"]) if len(sample) >= 3 else pd.NA
        rows.append({"window_days": window, "correlation": corr, "sample_size": len(sample)})
    return pd.DataFrame(rows)


def conditional_next_day_stats(pair_frame: pd.DataFrame, threshold: float = 0.01) -> pd.DataFrame:
    if pair_frame.empty:
        return pd.DataFrame()
    rows = []
    for label, mask in {
        "US +1%以上": pair_frame["us_return"] >= threshold,
        "US -1%以下": pair_frame["us_return"] <= -threshold,
    }.items():
        sample = pair_frame[mask]
        rows.append(
            {
                "condition": label,
                "count": len(sample),
                "japan_avg_return": sample["japan_return"].mean() if not sample.empty else pd.NA,
                "japan_up_rate": (sample["japan_return"] > 0).mean() if not sample.empty else pd.NA,
            }
        )
    return pd.DataFrame(rows)

