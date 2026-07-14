import pandas as pd
import pytest

from app.analysis.sec_fundamentals import normalize_sec_companyfacts, normalize_sec_ticker_directory
from app.collectors.sec import SecClient
from app.core.exceptions import DataProviderError


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


def test_sec_client_requires_identifying_user_agent(monkeypatch):
    client = SecClient()
    monkeypatch.setattr(client.settings, "sec_user_agent", "")
    with pytest.raises(DataProviderError, match="SEC_USER_AGENT"):
        client._headers()


def test_sec_client_rejects_malformed_cik():
    with pytest.raises(DataProviderError, match="10-digit"):
        SecClient().fetch_companyfacts("ABC")


def test_sec_client_fetch_companyfacts_uses_user_agent(monkeypatch):
    class Response:
        status_code = 200

        def json(self):
            return {"facts": {}}

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers):
            assert url.endswith("/api/xbrl/companyfacts/CIK0000000001.json")
            assert headers["User-Agent"] == "Market Signal Lab test@example.com"
            return Response()

    client = SecClient()
    monkeypatch.setattr(client.settings, "sec_user_agent", "Market Signal Lab test@example.com")
    monkeypatch.setattr("app.collectors.sec.httpx.Client", lambda timeout: Client())
    payload, _ = client.fetch_companyfacts("1")
    assert payload == {"facts": {}}


def test_sec_client_marks_rate_limit_retryable(monkeypatch):
    class Response:
        status_code = 429

        def json(self):
            return {}

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers):
            return Response()

    client = SecClient()
    monkeypatch.setattr(client.settings, "sec_user_agent", "Market Signal Lab test@example.com")
    monkeypatch.setattr("app.collectors.sec.httpx.Client", lambda timeout: Client())
    with pytest.raises(DataProviderError) as error:
        client.fetch_companyfacts("1")
    assert error.value.category == "rate_limited"
    assert error.value.retryable is True


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
