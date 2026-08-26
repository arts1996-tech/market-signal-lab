import pandas as pd
import pytest

from app.analysis.technical import (
    atr,
    bollinger_bands,
    completed_period_returns,
    daily_returns,
    distance_from_rolling_high,
    horizon_relative_strength,
    macd,
    short_term_indicator_frame,
    short_term_signal_snapshot,
    rsi,
    simple_moving_average,
    stochastic_oscillator,
    support_resistance_candidates,
)
from app.analysis.market_calendar import (
    exchange_calendar,
    is_next_exchange_session,
    next_exchange_session,
)


def test_daily_returns_does_not_forward_fill_missing_values():
    close = pd.Series([100.0, None, 110.0], index=pd.date_range("2024-01-01", periods=3))

    result = daily_returns(close)

    assert result.empty


def test_daily_returns_excludes_unverified_weekday_gaps_but_keeps_weekends():
    friday_to_monday = pd.Series(
        [100.0, 110.0], index=pd.to_datetime(["2024-01-05", "2024-01-08"])
    )
    friday_to_tuesday = pd.Series(
        [100.0, 110.0], index=pd.to_datetime(["2024-01-05", "2024-01-09"])
    )

    assert daily_returns(friday_to_monday).iloc[0] == pytest.approx(0.1)
    assert daily_returns(friday_to_tuesday).empty


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


def test_short_term_indicator_frame_adds_atr_only_from_valid_ohlc():
    sessions = exchange_calendar("XTKS").sessions_in_range(
        "2026-01-05", "2026-03-31"
    )[:30]
    close = pd.Series(range(100, 130), index=sessions, dtype=float)
    high = close + 2
    low = close - 1

    available = short_term_indicator_frame(close, high=high, low=low)
    unavailable = short_term_indicator_frame(close)

    assert available["atr_14"].dropna().iloc[-1] == pytest.approx(3.0)
    assert available["atr_pct_14"].dropna().iloc[-1] == pytest.approx(3 / 129)
    assert unavailable["atr_14"].isna().all()


def test_stochastic_oscillator_calculates_slow_14_3_3_with_sma_smoothing():
    high = pd.Series(range(1, 21), dtype=float)
    low = high - 10
    close = high - 2

    result = stochastic_oscillator(high, low, close)

    expected = 100 * 21 / 23
    assert result["stoch_raw_k_14"].dropna().iloc[-1] == pytest.approx(expected)
    assert result["stoch_k_14_3"].dropna().iloc[-1] == pytest.approx(expected)
    assert result["stoch_d_14_3_3"].dropna().iloc[-1] == pytest.approx(expected)
    assert result["stoch_d_14_3_3"].first_valid_index() == 17


def test_stochastic_oscillator_does_not_impute_missing_invalid_or_zero_range_ohlc():
    high = pd.Series([10.0] * 20)
    low = pd.Series([10.0] * 20)
    close = pd.Series([10.0] * 20)

    zero_range = stochastic_oscillator(high, low, close)
    missing = stochastic_oscillator(high.mask(high.index == 5), low, close)
    invalid = stochastic_oscillator(high, low, close.mask(close.index == 19, 11.0))

    assert zero_range.isna().all().all()
    assert pd.isna(missing.loc[13, "stoch_raw_k_14"])
    assert pd.isna(missing.loc[15, "stoch_k_14_3"])
    assert invalid.iloc[-1].isna().all()


def test_stochastic_oscillator_rejects_invalid_parameters():
    values = pd.Series(range(20), dtype=float)

    with pytest.raises(ValueError, match="positive integers"):
        stochastic_oscillator(values, values, values, high_low_window=0)


def test_stochastic_oscillator_names_custom_versioned_parameters():
    high = pd.Series(range(10, 30), dtype=float)
    low = high - 5
    close = high - 1

    result = stochastic_oscillator(
        high, low, close, high_low_window=5, k_smoothing=2, d_window=2
    )

    assert list(result.columns) == [
        "stoch_raw_k_5",
        "stoch_k_5_2",
        "stoch_d_5_2_2",
    ]


def test_short_term_indicator_frame_adds_stochastic_only_from_valid_ohlc():
    sessions = exchange_calendar("XTKS").sessions_in_range(
        "2026-01-05", "2026-03-31"
    )[:30]
    close = pd.Series(range(100, 130), index=sessions, dtype=float)
    high = close + 2
    low = close - 1

    available = short_term_indicator_frame(close, high=high, low=low)
    unavailable = short_term_indicator_frame(close)

    assert available["stoch_k_14_3"].dropna().iloc[-1] == pytest.approx(87.5)
    assert available["stoch_d_14_3_3"].dropna().iloc[-1] == pytest.approx(87.5)
    assert unavailable["stoch_k_14_3"].isna().all()
    assert unavailable["stoch_d_14_3_3"].isna().all()


def test_support_resistance_candidates_use_confirmed_past_swings_and_price_bands():
    closes = [
        110, 112, 115, 112, 108, 105, 108, 112, 115, 112, 108, 105,
        108, 112, 115, 112, 108, 105, 108, 112, 115, 112, 108, 105,
        108, 112, 115, 112, 108, 105, 108, 112, 115, 112, 108, 110,
    ]
    index = pd.date_range("2026-01-01", periods=len(closes), tz="UTC")
    close = pd.Series(closes, index=index, dtype=float)

    result = support_resistance_candidates(close + 2, close - 2, close)
    candidates = result["candidates"]

    support = candidates[candidates["level_type"] == "support"].iloc[0]
    resistance = candidates[candidates["level_type"] == "resistance"].iloc[0]
    assert result["quality_reasons"] == []
    assert support["price_level"] == pytest.approx(103)
    assert support["touch_count"] >= 2
    assert support["status"] == "active"
    assert support["invalidation_price"] == pytest.approx(103 * 0.985)
    assert resistance["price_level"] == pytest.approx(117)
    assert resistance["touch_count"] >= 2
    assert resistance["status"] == "active"
    assert resistance["last_touch_time"] <= index[-3]


def test_support_resistance_candidates_do_not_fill_missing_ohlc_or_accept_invalid_parameters():
    index = pd.date_range("2026-01-01", periods=30, tz="UTC")
    close = pd.Series(range(100, 130), index=index, dtype=float)

    missing = support_resistance_candidates(None, None, close)
    interrupted = support_resistance_candidates(
        close + 2, (close - 2).mask((close - 2).index == index[10]), close
    )

    assert missing["candidates"].empty
    assert missing["quality_reasons"] == [
        "support_resistance_unavailable_missing_valid_ohlc"
    ]
    assert interrupted["candidates"].empty
    assert interrupted["quality_reasons"] == [
        "support_resistance_insufficient_contiguous_valid_ohlc"
    ]
    with pytest.raises(ValueError, match="min_observations"):
        support_resistance_candidates(close + 2, close - 2, close, lookback=30, min_observations=31)


def test_completed_period_returns_excludes_incomplete_week_and_month():
    sessions = exchange_calendar("XTKS").sessions_in_range(
        "2026-03-01", "2026-05-13"
    )
    close = pd.Series(range(100, 100 + len(sessions)), index=sessions, dtype=float)

    weekly = completed_period_returns(close, "weekly")
    monthly = completed_period_returns(close, "monthly")

    assert weekly.index[-1].tz_localize(None) == pd.Timestamp("2026-05-08")
    assert monthly.index[-1].tz_localize(None) == pd.Timestamp("2026-04-30")
    march_end = sessions[sessions.to_period("M") == pd.Period("2026-03")][-1]
    april_end = sessions[sessions.to_period("M") == pd.Period("2026-04")][-1]
    assert monthly.iloc[-1] == pytest.approx(close.loc[april_end] / close.loc[march_end] - 1)


def test_relative_strength_requires_same_complete_exchange_sessions():
    sessions = exchange_calendar("XTKS").sessions_in_range(
        "2026-04-01", "2026-05-31"
    )[:21]
    asset = pd.Series(
        [100 + index for index in range(21)], index=sessions, dtype=float
    )
    benchmark = pd.Series(
        [100 + index / 2 for index in range(21)], index=sessions, dtype=float
    )

    result = horizon_relative_strength(asset, benchmark, horizon=20)
    missing = horizon_relative_strength(asset, benchmark.drop(sessions[10]), horizon=20)

    assert result["asset_return"] == pytest.approx(0.20)
    assert result["benchmark_return"] == pytest.approx(0.10)
    assert result["relative_strength"] == pytest.approx(0.10)
    assert result["sessions"] == 20
    assert missing["relative_strength"] is None


def test_distance_from_52week_high_requires_full_window():
    close = pd.Series(
        range(1, 253),
        index=pd.date_range("2025-01-01", periods=252, freq="D"),
        dtype=float,
    )

    assert distance_from_rolling_high(close.iloc[:-1]) is None
    assert distance_from_rolling_high(close) == pytest.approx(0.0)


def test_short_term_signal_snapshot_handles_an_empty_frame_without_close_column():
    snapshot = short_term_signal_snapshot(pd.DataFrame())

    assert snapshot["label"] == "データ不足"
    assert snapshot["negative_reasons"] == ["価格データが不足しています"]


def test_exchange_calendar_handles_nyse_holiday_and_jpx_next_session():
    assert is_next_exchange_session("2026-04-02", "2026-04-06", "XNYS")  # Good Friday
    assert is_next_exchange_session("2026-04-02", "2026-04-03", "XTKS")
    assert next_exchange_session("2026-05-01", "XTKS") == pd.Timestamp("2026-05-07")
