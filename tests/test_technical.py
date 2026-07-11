import pandas as pd

from app.analysis.technical import (
    atr,
    bollinger_bands,
    daily_returns,
    macd,
    short_term_indicator_frame,
    short_term_signal_snapshot,
    rsi,
    simple_moving_average,
)


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


def test_moving_average_and_bollinger_bands():
    close = pd.Series(range(1, 31), dtype=float)

    ma = simple_moving_average(close, 5)
    bands = bollinger_bands(close, 20)

    assert ma.dropna().iloc[0] == 3
    assert {"bb_upper", "bb_middle", "bb_lower"} == set(bands.columns)
    assert bands["bb_upper"].dropna().iloc[-1] > bands["bb_lower"].dropna().iloc[-1]


def test_short_term_indicator_frame_and_snapshot():
    close = pd.Series(range(100, 180), dtype=float)

    indicators = short_term_indicator_frame(close)
    snapshot = short_term_signal_snapshot(indicators)

    assert "sma_20" in indicators.columns
    assert "rsi_14" in indicators.columns
    assert "macd" in indicators.columns
    assert 0 <= snapshot["score"] <= 100
    assert snapshot["label"]
