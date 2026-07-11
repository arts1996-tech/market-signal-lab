import pandas as pd


def daily_returns(close: pd.Series) -> pd.Series:
    return close.sort_index().pct_change(fill_method=None).dropna()


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

