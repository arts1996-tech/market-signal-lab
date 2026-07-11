import pandas as pd

from app.analysis.technical import atr, daily_returns, macd, rsi


def test_daily_returns_does_not_forward_fill_missing_values():
    close = pd.Series([100.0, None, 110.0], index=pd.date_range("2024-01-01", periods=3))

    result = daily_returns(close)

    assert result.empty


def test_rsi_bounds():
    close = pd.Series(range(1, 31), dtype=float)

    result = rsi(close).dropna()

    assert (result >= 0).all()
    assert (result <= 100).all()


def test_macd_columns():
    close = pd.Series(range(1, 60), dtype=float)

    result = macd(close)

    assert list(result.columns) == ["macd", "signal", "histogram"]
    assert len(result) == len(close)


def test_atr_uses_true_range():
    high = pd.Series([11, 12, 13, 14, 15], dtype=float)
    low = pd.Series([9, 10, 11, 12, 13], dtype=float)
    close = pd.Series([10, 11, 12, 13, 14], dtype=float)

    result = atr(high, low, close, window=3)

    assert result.dropna().iloc[0] == 2

