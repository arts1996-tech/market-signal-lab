import pandas as pd

from app.analysis.fundamentals import derive_fundamental_metrics, fundamentals_as_of, normalize_financial_summary


def test_normalize_financial_summary_requires_disclosure_and_converts_numbers():
    result = normalize_financial_summary([
        {"Code": "13060", "DisclosedDate": "2026-05-01", "CurrentPeriodEndDate": "2026-03-31", "Sales": "1000", "EarningsPerShare": "12.5"},
        {"Code": "13060", "Sales": "999"},
    ])

    assert len(result) == 1
    assert result.iloc[0]["symbol"] == "13060"
    assert result.iloc[0]["sales"] == 1000.0
    assert result.iloc[0]["eps"] == 12.5


def test_fundamentals_as_of_excludes_future_disclosures():
    frame = pd.DataFrame([
        {"disclosed_at": pd.Timestamp("2026-04-01", tz="UTC"), "sales": 100},
        {"disclosed_at": pd.Timestamp("2026-05-01", tz="UTC"), "sales": 200},
    ])
    result = fundamentals_as_of(frame, pd.Timestamp("2026-04-15", tz="UTC"))
    assert result["sales"].tolist() == [100]


def test_derive_fundamental_metrics_does_not_approximate_missing_book_value():
    result = derive_fundamental_metrics(
        {"eps": 10, "net_income": 20, "equity": 100, "sales": 200, "operating_profit": 30},
        price=150,
    )
    assert result["per"] == 15.0
    assert result["pbr"] is None
    assert result["roe"] == 0.2
    assert result["operating_margin"] == 0.15
