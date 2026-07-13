from app.analysis.fundamentals import normalize_financial_summary


def test_normalize_financial_summary_requires_disclosure_and_converts_numbers():
    result = normalize_financial_summary([
        {"Code": "13060", "DisclosedDate": "2026-05-01", "CurrentPeriodEndDate": "2026-03-31", "Sales": "1000", "EarningsPerShare": "12.5"},
        {"Code": "13060", "Sales": "999"},
    ])

    assert len(result) == 1
    assert result.iloc[0]["symbol"] == "13060"
    assert result.iloc[0]["sales"] == 1000.0
    assert result.iloc[0]["eps"] == 12.5
