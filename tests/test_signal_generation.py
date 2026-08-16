import pandas as pd

from app.analysis.market_calendar import exchange_calendar
from app.analysis.signal_generation import (
    FUTURE_OUTCOME_FIELDS,
    generate_signals_as_of,
)


def _rows(symbol: str, dates: pd.DatetimeIndex, *, base: float, source: str) -> list[dict]:
    return [
        {
            "symbol": symbol,
            "name": symbol,
            "price_time": date,
            "open": base + index,
            "close": base + index,
            "source": source,
            "fetched_at": date,
        }
        for index, date in enumerate(dates)
    ]


def _inputs(periods: int = 40) -> tuple[pd.DataFrame, pd.DataFrame, pd.DatetimeIndex]:
    dates = exchange_calendar("XTKS").sessions_in_range("2026-01-01", "2026-06-30")[
        :periods
    ]
    index_prices = pd.DataFrame(
        _rows("NASDAQCOM", dates, base=100, source="fred")
        + _rows("NIKKEI225", dates, base=200, source="fred")
    )
    japan_prices = pd.DataFrame(_rows("86970", dates, base=300, source="jquants"))
    return index_prices, japan_prices, dates


def _utc(value) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")


def test_generate_signals_as_of_does_not_require_future_prices():
    index_prices, japan_prices, dates = _inputs(30)

    result = generate_signals_as_of(
        index_prices,
        japan_prices,
        as_of=dates[-1],
        score_threshold=0,
        min_observations=30,
    )

    assert result["observation_status"] == "eligible_signals"
    assert len(result["signals"]) == 1
    assert result["signals"].iloc[0]["data_as_of"] == _utc(dates[-1])
    assert result["signals"].iloc[0]["entry_date"] > _utc(dates[-1])
    assert not FUTURE_OUTCOME_FIELDS.intersection(result["signals"].columns)


def test_generate_signals_as_of_ignores_future_market_rows():
    index_prices, japan_prices, dates = _inputs(40)
    cutoff = dates[29]

    full = generate_signals_as_of(
        index_prices,
        japan_prices,
        as_of=cutoff,
        score_threshold=0,
        min_observations=30,
    )
    trimmed = generate_signals_as_of(
        index_prices[index_prices["price_time"] <= cutoff],
        japan_prices[japan_prices["price_time"] <= cutoff],
        as_of=cutoff,
        score_threshold=0,
        min_observations=30,
    )

    columns = ["symbol", "score", "direction", "decision", "status", "data_as_of"]
    pd.testing.assert_frame_equal(
        full["decisions"][columns].reset_index(drop=True),
        trimmed["decisions"][columns].reset_index(drop=True),
    )
    assert pd.to_datetime(full["known_japan_prices"]["price_time"], utc=True).max() == _utc(cutoff)


def test_generate_signals_as_of_excludes_rows_not_available_at_decision_time():
    index_prices, japan_prices, dates = _inputs(31)
    cutoff = dates[-1]
    japan_prices["available_at"] = japan_prices["fetched_at"]
    japan_prices.loc[japan_prices.index[-1], "available_at"] = cutoff + pd.Timedelta(days=1)

    result = generate_signals_as_of(
        index_prices,
        japan_prices,
        as_of=cutoff,
        score_threshold=0,
        min_observations=30,
    )

    assert len(result["known_japan_prices"]) == 30
    assert result["decisions"].iloc[0]["data_as_of"] == _utc(dates[-2])


def test_generate_signals_as_of_records_insufficient_data_without_outcome():
    index_prices, japan_prices, dates = _inputs(29)

    result = generate_signals_as_of(
        index_prices,
        japan_prices,
        as_of=dates[-1],
        score_threshold=70,
        min_observations=30,
    )

    assert result["observation_status"] == "insufficient_data"
    assert result["signals"].empty
    assert set(result["decisions"]["decision"]) == {"データ不足"}
    assert set(result["decisions"]["status"]) == {"insufficient_data"}


def test_generate_signals_as_of_records_wait_when_score_gate_fails():
    index_prices, japan_prices, dates = _inputs(30)

    result = generate_signals_as_of(
        index_prices,
        japan_prices,
        as_of=dates[-1],
        score_threshold=100,
        min_observations=30,
    )

    assert result["observation_status"] == "no_eligible_signals"
    assert result["signals"].empty
    assert set(result["decisions"]["decision"]) == {"待機"}
    assert set(result["decisions"]["status"]) == {"below_score_threshold"}
