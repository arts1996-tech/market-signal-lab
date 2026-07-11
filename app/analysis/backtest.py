import pandas as pd


def forward_returns(close: pd.Series, signal_dates: pd.Index, horizons: list[int] | None = None) -> pd.DataFrame:
    horizons = horizons or [1, 5, 10, 20]
    ordered = close.sort_index()
    rows = []
    for signal_date in signal_dates:
        if signal_date not in ordered.index:
            continue
        location = ordered.index.get_loc(signal_date)
        if not isinstance(location, int):
            continue
        row = {"signal_date": signal_date, "entry_price": ordered.iloc[location]}
        for horizon in horizons:
            future_location = location + horizon
            row[f"return_{horizon}d"] = (
                ordered.iloc[future_location] / ordered.iloc[location] - 1
                if future_location < len(ordered)
                else pd.NA
            )
        rows.append(row)
    return pd.DataFrame(rows)

