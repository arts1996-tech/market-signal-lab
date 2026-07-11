import pandas as pd
import pytest
import httpx

from app.collectors.jquants import (
    build_http_error_message,
    find_record_list,
    JQuantsClient,
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


def test_parse_listed_info_response_builds_assets():
    payload = {
        "info": [
            {
                "Code": "86970",
                "CompanyName": "日本取引所グループ",
                "MarketCodeName": "プライム",
                "Sector17CodeName": "金融（除く銀行）",
                "Sector33CodeName": "その他金融業",
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
    assert assets[1]["asset_type"] == "etf"


def test_parse_listed_info_response_reports_unparsable_keys():
    with pytest.raises(DataProviderError, match="No parsable J-Quants listed info"):
        parse_listed_info_response({"info": [{"foo": "bar"}]})


def test_listed_info_endpoint_candidates_try_equities_path_first():
    assert JQuantsClient.listed_info_endpoints[0] == "/v2/equities/listed/info"
    assert "/v2/listed/info" in JQuantsClient.listed_info_endpoints
