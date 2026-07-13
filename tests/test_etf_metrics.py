from app.analysis.etf_metrics import normalize_etf_metrics


def test_normalize_etf_metrics_keeps_provider_values_only():
    result = normalize_etf_metrics([{"Code": "13060", "Date": "2026-04-01", "ExpenseRatio": "0.15", "TrackingIndex": "TOPIX"}])
    assert len(result) == 1
    assert result.iloc[0]["expense_ratio"] == 0.15
    assert result.iloc[0]["tracking_index"] == "TOPIX"
