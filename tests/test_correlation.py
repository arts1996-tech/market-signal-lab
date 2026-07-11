import pandas as pd

from app.analysis.correlation import (
    close_wide,
    horizon_correlations,
    normalized_index,
    rolling_correlation,
    us_japan_pair_frame,
)
from app.services.analysis_service import build_us_japan_correlation_records


def _prices():
    dates = pd.to_datetime(
        ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"], utc=True
    )
    rows = []
    for symbol, values in {
        "NASDAQCOM": [100, 101, 100, 102, 103],
        "NIKKEI225": [200, 202, 201, 205, 207],
    }.items():
        for date, close in zip(dates, values, strict=True):
            rows.append(
                {
                    "symbol": symbol,
                    "name": symbol,
                    "price_time": date,
                    "close": close,
                    "source": "sample",
                    "fetched_at": date,
                }
            )
    return pd.DataFrame(rows)


def test_close_wide_and_normalized_index():
    wide = close_wide(_prices())
    normalized = normalized_index(wide)

    assert wide.loc[pd.Timestamp("2024-01-02", tz="UTC"), "NASDAQCOM"] == 100
    assert normalized.loc[pd.Timestamp("2024-01-02", tz="UTC"), "NIKKEI225"] == 100


def test_us_previous_day_alignment_avoids_same_day_us_close():
    wide = close_wide(_prices())

    pair = us_japan_pair_frame(wide, "NASDAQCOM", "NIKKEI225")

    first = pair.iloc[0]
    assert first["japan_date"] == pd.Timestamp("2024-01-04", tz="UTC")
    assert first["us_date"] == pd.Timestamp("2024-01-03", tz="UTC")


def test_horizon_and_rolling_correlation():
    wide = close_wide(_prices())
    pair = us_japan_pair_frame(wide, "NASDAQCOM", "NIKKEI225")

    corr = horizon_correlations(pair, [3])
    rolling = rolling_correlation(pair, 2)

    assert corr.loc[0, "sample_size"] == 3
    assert len(rolling) == 2


def test_build_us_japan_correlation_records_include_pair_and_average_rows():
    wide = close_wide(_prices())

    records = build_us_japan_correlation_records(
        wide,
        us_symbols=["NASDAQCOM"],
        japan_symbols=["NIKKEI225"],
    )

    pair_rows = [row for row in records if row["analysis_name"] == "us_japan_index_correlation"]
    average_rows = [
        row for row in records if row["analysis_name"] == "us_japan_index_correlation_average"
    ]
    group_mean_rows = [
        row
        for row in records
        if row["analysis_name"] == "us_japan_index_group_average_correlation"
    ]
    assert pair_rows
    assert average_rows
    assert group_mean_rows
    assert {row["window_days"] for row in pair_rows} == {20, 60, 120, 250}
    assert average_rows[0]["base_symbol"] == "US_INDEX_AVERAGE"
    assert average_rows[0]["target_symbol"] == "JP_INDEX_AVERAGE"
    assert group_mean_rows[0]["method"] == "pearson_on_group_mean_returns"
    assert average_rows[0]["lag_rule"] == "us_previous_trading_day_to_japan_current_day"
