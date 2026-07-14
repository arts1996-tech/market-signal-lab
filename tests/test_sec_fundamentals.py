import pandas as pd

from app.analysis.sec_fundamentals import normalize_sec_companyfacts, normalize_sec_ticker_directory


def test_normalize_sec_ticker_directory_validates_cik_and_deduplicates_symbols():
    result = normalize_sec_ticker_directory({
        "data": [
            ["1234", "Example Inc", "exm"],
            ["1234", "Duplicate", "EXM"],
            ["bad", "Invalid", "BAD"],
            ["5678", "Other", "OTH"],
        ]
    })
    assert result["symbol"].tolist() == ["EXM", "OTH"]
    assert result.iloc[0]["cik"] == "0000001234"


def test_normalize_sec_companyfacts_preserves_filing_timing_and_known_values():
    payload = {
        "cik": "0000001",
        "facts": {"us-gaap": {
            "Revenues": {"units": {"USD": [{"val": 1000, "end": "2025-12-31", "filed": "2026-02-15"}]}},
            "OperatingIncomeLoss": {"units": {"USD": [{"val": 100, "end": "2025-12-31", "filed": "2026-02-15"}]}},
            "Assets": {"units": {"USD": [{"val": 5000, "end": "2025-12-31", "filed": "2026-02-15"}]}},
        }},
    }
    result = normalize_sec_companyfacts(payload, symbol="AAPL")
    assert len(result) == 1
    assert result.iloc[0]["symbol"] == "AAPL"
    assert result.iloc[0]["sales"] == 1000.0
    assert result.iloc[0]["operating_profit"] == 100.0
    assert result.iloc[0]["total_assets"] == 5000.0
    assert result.iloc[0]["disclosed_at"] == pd.Timestamp("2026-02-15", tz="UTC")


def test_normalize_sec_companyfacts_does_not_invent_missing_fields_or_accept_bad_values():
    payload = {
        "facts": {"us-gaap": {
            "Assets": {"units": {"USD": [
                {"val": "bad", "end": "2025-12-31", "filed": "2026-02-15"},
                {"val": 10, "end": "2025-12-31", "filed": "not-a-date"},
            ]}},
        }},
    }
    result = normalize_sec_companyfacts(payload, symbol="TEST")
    assert result.empty
