import pandas as pd
import pytest

from app.collectors.jquants import find_record_list, parse_daily_bars_response
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


def test_parse_daily_bars_response_rejects_unexpected_payload():
    with pytest.raises(DataProviderError):
        parse_daily_bars_response("86970", {"unexpected": {}})


def test_find_record_list_accepts_common_keys():
    assert find_record_list({"bars": [{"Close": 1}]}) == [{"Close": 1}]
    assert find_record_list({"data": [{"Close": 1}]}) == [{"Close": 1}]
