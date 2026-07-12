"""Deterministic sector and symbol sensitivity summaries for spillover data."""

import pandas as pd


def _sensitivity_rows(data: pd.DataFrame, group_column: str, min_samples: int) -> pd.DataFrame:
    columns = [group_column, "symbol_count", "sample_size", "mean_return", "correlation", "slope"]
    if data.empty or not {group_column, "symbol", "us_return", "target_return"}.issubset(data.columns):
        return pd.DataFrame(columns=columns)
    rows = []
    for group, sample in data.dropna(subset=[group_column, "us_return", "target_return"]).groupby(group_column):
        if len(sample) < min_samples:
            continue
        x = sample["us_return"].astype(float)
        y = sample["target_return"].astype(float)
        variance = float(x.var(ddof=0))
        rows.append({
            group_column: group,
            "symbol_count": int(sample["symbol"].nunique()),
            "sample_size": int(len(sample)),
            "mean_return": float(y.mean()),
            "correlation": None if variance == 0 else float(x.corr(y)),
            "slope": None if variance == 0 else float(((x - x.mean()) * (y - y.mean())).mean() / variance),
        })
    return pd.DataFrame(rows, columns=columns).sort_values("sample_size", ascending=False)


def sector_sensitivity(data: pd.DataFrame, min_samples: int = 10) -> dict[str, pd.DataFrame]:
    """Return sector and symbol sensitivity without imputation or prediction."""
    return {
        "sector": _sensitivity_rows(data, "sector", min_samples),
        "symbol": _sensitivity_rows(data, "symbol", min_samples),
    }
