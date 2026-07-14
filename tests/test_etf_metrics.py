from app.analysis.etf_metrics import normalize_etf_metrics


def test_normalize_etf_metrics_keeps_provider_values_only():
    result = normalize_etf_metrics([{"Code": "13060", "Date": "2026-04-01", "ExpenseRatio": "0.15", "TrackingIndex": "TOPIX"}])
    assert len(result) == 1
    assert result.iloc[0]["expense_ratio"] == 0.15
    assert result.iloc[0]["tracking_index"] == "TOPIX"


def test_normalize_etf_metrics_skips_invalid_dates_and_nulls_bad_numbers():
    result = normalize_etf_metrics([
        {"Code": "13060", "Date": "not-a-date", "ExpenseRatio": "0.1"},
        {"Code": "13060", "Date": "2026-04-01", "ExpenseRatio": "unknown", "NetAssets": "-"},
    ])
    assert len(result) == 1
    assert result.iloc[0]["expense_ratio"] is None
    assert result.iloc[0]["net_assets"] is None
