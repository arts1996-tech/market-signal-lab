import pandas as pd
import pytest
import httpx

from app.collectors.jquants import (
    build_http_error_message,
    find_record_list,
    JQuantsClient,
    jquants_error_category,
    parse_daily_bars_response,
    parse_listed_info_response,
)
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


def test_parse_daily_bars_response_handles_jquants_abbreviated_payload():
    payload = {
        "daily_bars": [
            {
                "Code": "86970",
                "Date": "2026-04-01",
                "O": 1600,
                "H": 1660,
                "L": 1585,
                "C": 1620,
                "Vo": 1200000,
                "AdjO": 1600,
                "AdjH": 1660,
                "AdjL": 1585,
                "AdjC": 1620,
                "AdjVo": 1200000,
            }
        ]
    }

    frame = parse_daily_bars_response("86970", payload)

    assert len(frame) == 1
    assert frame.loc[0, "open"] == 1600
    assert frame.loc[0, "high"] == 1660
    assert frame.loc[0, "low"] == 1585
    assert frame.loc[0, "close"] == 1620
    assert frame.loc[0, "adjusted_close"] == 1620
    assert frame.loc[0, "volume"] == 1200000


def test_parse_daily_bars_preserves_raw_and_adjusted_ohlc_separately():
    frame = parse_daily_bars_response(
        "86970",
        {
            "daily_bars": [
                {
                    "Date": "2024-01-04", "O": 100, "H": 110, "L": 90, "C": 100,
                    "AdjO": 50, "AdjH": 55, "AdjL": 45, "AdjC": 50, "AdjVo": 2000, "AdjFactor": 0.5,
                }
            ]
        },
    )

    assert frame.loc[0, "close"] == 100
    assert frame.loc[0, "adjusted_close"] == 50
    assert frame.loc[0, "adjusted_open"] == 50
    assert frame.loc[0, "adjustment_factor"] == 0.5
    assert frame.loc[0, "price_basis"] == "raw_ohlcv_with_adjusted"


def test_parse_daily_bars_response_returns_empty_frame_for_empty_records():
    frame = parse_daily_bars_response("86970", {"daily_bars": []})

    assert frame.empty


def test_parse_daily_bars_response_treats_null_provider_price_as_no_data():
    frame = parse_daily_bars_response(
        "13190",
        {"data": [{"Date": "2026-04-10", "Code": "13190", "O": None, "H": None, "L": None, "C": None, "Vo": None}]},
    )

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


def test_jquants_error_categories_keep_temporary_failures_distinct():
    assert jquants_error_category(429) == "rate_limited"
    assert jquants_error_category(503) == "provider_unavailable"
    assert jquants_error_category(401) == "authentication_error"
    assert jquants_error_category(400) == "invalid_request"


def test_parse_listed_info_response_builds_assets():
    payload = {
        "info": [
            {
                "Code": "86970",
                "CompanyName": "日本取引所グループ",
                "MarketCodeName": "プライム",
                "Sector17CodeName": "金融（除く銀行）",
                "Sector33CodeName": "その他金融業",
                "Date": "20260724",
                "ListingDate": "20040101",
            },
            {
                "Code": "13060",
                "CompanyName": "ＮＥＸＴ　ＦＵＮＤＳ　ＴＯＰＩＸ連動型上場投信",
                "MarketCodeName": "ETF・ETN",
            },
        ]
    }

    assets = parse_listed_info_response(payload)

    assert assets[0]["symbol"] == "86970"
    assert assets[0]["name"] == "日本取引所グループ"
    assert assets[0]["asset_type"] == "stock"
    assert assets[0]["metadata_json"]["market"] == "プライム"
    assert assets[0]["metadata_json"]["lifecycle"] == {
        "effective_date": "20260724",
        "listed_on": "20040101",
        "delisted_on": None,
    }
    assert assets[1]["asset_type"] == "etf"


def test_parse_listed_info_response_reports_unparsable_keys():
    with pytest.raises(DataProviderError, match="No parsable J-Quants listed info"):
        parse_listed_info_response({"info": [{"foo": "bar"}]})


def test_listed_info_endpoint_candidates_try_equities_path_first():
    assert JQuantsClient.listed_info_endpoints[0] == "/v2/equities/master"
    assert "/v2/listed/info" in JQuantsClient.listed_info_endpoints


def test_parse_listed_info_response_accepts_master_key():
    assets = parse_listed_info_response({"master": [{"Code": "86970", "CompanyName": "日本取引所グループ"}]})

    assert assets[0]["symbol"] == "86970"


def test_fetch_daily_bars_follows_pagination_key(monkeypatch):
    class Response:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class Client:
        calls = []

        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, params, headers):
            self.calls.append(params.copy())
            if len(self.calls) == 1:
                return Response({"daily_bars": [{"Date": "2026-04-01", "C": 100}], "pagination_key": "next"})
            return Response({"daily_bars": [{"Date": "2026-04-02", "C": 101}]})

    monkeypatch.setattr("app.collectors.jquants.httpx.Client", Client)
    client = JQuantsClient()
    monkeypatch.setattr(client, "_headers", lambda: {})
    frame, _ = client.fetch_daily_bars("86970", from_date="20260401", to_date="20260402")

    assert len(frame) == 2
    assert Client.calls[1]["pagination_key"] == "next"
