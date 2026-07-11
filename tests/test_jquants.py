import pandas as pd
import pytest
import httpx

from app.collectors.jquants import build_http_error_message, find_record_list, parse_daily_bars_response
from app.core.exceptions import DataProviderError


def test_parse_daily_bars_response_handles_jquants_style_payload():
    payload = {
        "daily_quotes": [
            {
                "Date": "2024-01-04",
                "Open": 1000,
                "High": 1100,
                "Low": 990,
                "Close": 1080,
                "Volume": 123400,
                "AdjustmentClose": 1080,
            }
        ]
    }

    frame = parse_daily_bars_response("86970", payload)

    assert len(frame) == 1
    assert frame.loc[0, "symbol"] == "86970"
    assert frame.loc[0, "close"] == 1080
    assert frame.loc[0, "volume"] == 123400
    assert pd.Timestamp(frame.loc[0, "price_time"]).tzinfo is not None


def test_parse_daily_bars_response_handles_daily_bars_snake_case_payload():
    payload = {
        "daily_bars": [
            {
                "base_date": "2024-01-04",
                "open": 1000,
                "high": 1100,
                "low": 990,
                "close": 1080,
                "volume": 123400,
                "adjustment_close": 1082,
            }
        ]
    }

    frame = parse_daily_bars_response("86970", payload)

    assert len(frame) == 1
    assert frame.loc[0, "close"] == 1080
    assert frame.loc[0, "adjusted_close"] == 1082


def test_parse_daily_bars_response_returns_empty_frame_for_empty_records():
    frame = parse_daily_bars_response("86970", {"daily_bars": []})

    assert frame.empty


def test_parse_daily_bars_response_reports_unparsable_record_keys():
    with pytest.raises(DataProviderError, match="No parsable J-Quants daily bars"):
        parse_daily_bars_response("86970", {"daily_bars": [{"foo": "bar"}]})


def test_parse_daily_bars_response_rejects_unexpected_payload():
    with pytest.raises(DataProviderError):
        parse_daily_bars_response("86970", {"unexpected": {}})


def test_find_record_list_accepts_common_keys():
    assert find_record_list({"bars": [{"Close": 1}]}) == [{"Close": 1}]
    assert find_record_list({"daily_bars": [{"Close": 1}]}) == [{"Close": 1}]
    assert find_record_list({"data": [{"Close": 1}]}) == [{"Close": 1}]


def test_build_http_error_message_summarizes_bad_request():
    response = httpx.Response(
        400,
        text='{"message":"date is outside available range"}',
        request=httpx.Request("GET", "https://api.jquants.com/v2/equities/bars/daily"),
    )

    message = build_http_error_message(response)

    assert "J-Quants rejected request (400)" in message
    assert "code/date" in message
    assert "outside available range" in message
