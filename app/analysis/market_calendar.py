import pandas as pd


def align_us_previous_to_japan(us_returns: pd.Series, japan_returns: pd.Series) -> pd.DataFrame:
    """Map each Japan trading day to the latest available earlier US trading day."""
    us = us_returns.dropna().sort_index()
    jp = japan_returns.dropna().sort_index()
    rows = []
    us_dates = list(us.index)
    cursor = 0
    for jp_date, jp_return in jp.items():
        while cursor < len(us_dates) and us_dates[cursor] < jp_date:
            cursor += 1
        if cursor == 0:
            continue
        us_date = us_dates[cursor - 1]
        rows.append(
            {
                "japan_date": jp_date,
                "us_date": us_date,
                "us_return": us.loc[us_date],
                "japan_return": jp_return,
            }
        )
    return pd.DataFrame(rows)

