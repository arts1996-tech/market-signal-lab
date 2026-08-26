import pandas as pd

from app.analysis.market_calendar import exchange_calendar
from app.analysis.screening import screen_assets, technical_attention_snapshot
from app.analysis.technical import short_term_indicator_frame


def _xtks_sessions(start: str, end: str, count: int) -> pd.DatetimeIndex:
    return exchange_calendar("XTKS").sessions_in_range(start, end)[:count]


def test_screen_assets_requires_history_and_preserves_asset_type():
    dates = _xtks_sessions("2024-01-01", "2024-05-31", 55)
    prices = pd.DataFrame({"symbol": "13060", "price_time": dates, "close": range(100, 155)})
    assets = pd.DataFrame([{"symbol": "13060", "name": "ETF", "asset_type": "etf", "metadata_json": {"sector_33": "ETF"}}])

    result = screen_assets(prices, assets, min_history=50)

    assert len(result) == 1
    assert result.iloc[0]["asset_type"] == "etf"
    assert result.iloc[0]["sector"] == "ETF"


def test_screen_assets_default_gate_is_30_distinct_valid_observations():
    dates = _xtks_sessions("2024-01-01", "2024-03-31", 30)
    prices = pd.DataFrame(
        {
            "symbol": "13060",
            "price_time": dates,
            "close": range(100, 130),
            "price_basis": "raw_ohlcv_with_adjusted",
        }
    )
    assets = pd.DataFrame(
        [
            {
                "symbol": "13060",
                "name": "ETF",
                "asset_type": "etf",
                "metadata_json": {},
            }
        ]
    )

    result = screen_assets(prices, assets)

    assert len(result) == 1
    assert result.iloc[0]["observations"] == 30
    assert result.iloc[0]["price_basis"] == "raw_ohlcv_with_adjusted"
    assert result.iloc[0]["data_as_of"] == pd.to_datetime(dates[-1], utc=True)

    duplicate_only = pd.concat([prices.iloc[:-1], prices.iloc[[-2]]], ignore_index=True)
    assert screen_assets(duplicate_only, assets).empty


def test_screen_assets_rejects_observation_count_that_crosses_a_session_gap():
    old_dates = _xtks_sessions("2024-01-01", "2024-03-31", 20)
    recent_dates = _xtks_sessions("2026-04-01", "2026-05-31", 15)
    dates = old_dates.append(recent_dates)
    prices = pd.DataFrame(
        {
            "symbol": "13060",
            "price_time": dates,
            "close": range(100, 135),
            "price_basis": "raw_ohlcv_with_adjusted",
        }
    )
    assets = pd.DataFrame(
        [{"symbol": "13060", "name": "ETF", "asset_type": "etf", "metadata_json": {}}]
    )

    assert screen_assets(prices, assets).empty


def test_attention_score_is_explainable_and_not_a_trade_label():
    snapshot = technical_attention_snapshot(
        pd.Series(
            {
                "close": 120,
                "return_20d": 0.12,
                "volatility_20d": 0.035,
                "rsi_14": 75,
                "bb_upper": 119,
                "bb_lower": 90,
                "histogram": 1.5,
            }
        ),
        observations=30,
    )

    assert snapshot["attention_score"] == 100
    assert snapshot["attention_label"] == "高い注目度"
    assert any("20日騰落率" in reason for reason in snapshot["attention_reasons"])
    assert "50日・75日移動平均は未算出" in snapshot["quality_warnings"]
    assert "買い" not in snapshot["attention_label"]


def test_screen_assets_adds_completed_returns_atr_relative_strength_and_52week():
    sessions = exchange_calendar("XTKS").sessions_in_range(
        "2025-01-01", "2026-06-30"
    )[:260]
    symbols = ["10010", "10020", "10030"]
    assets = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "name": symbol,
                "asset_type": "stock",
                "metadata_json": {"sector_33": "テスト業種"},
            }
            for symbol in symbols
        ]
    )
    rows = []
    for symbol_index, symbol in enumerate(symbols):
        for day_index, session in enumerate(sessions):
            close = 100 + day_index * (1 + symbol_index * 0.25)
            rows.append(
                {
                    "symbol": symbol,
                    "price_time": session,
                    "close": close,
                    "high": close + 2,
                    "low": close - 1,
                    "price_basis": "raw_ohlcv_with_adjusted",
                }
            )
    prices = pd.DataFrame(rows)
    benchmark_prices = pd.DataFrame(
        {
            "symbol": "NIKKEI225",
            "price_time": sessions,
            "close": [100 + day_index * 0.5 for day_index in range(len(sessions))],
        }
    )

    result = screen_assets(
        prices,
        assets,
        benchmark_prices=benchmark_prices,
    )

    assert len(result) == 3
    assert result["weekly_return"].notna().all()
    assert result["monthly_return"].notna().all()
    assert result["atr_14"].notna().all()
    assert result["stoch_raw_k_14"].notna().all()
    assert result["stoch_k_14_3"].notna().all()
    assert result["stoch_d_14_3_3"].notna().all()
    assert result["relative_strength_vs_benchmark_20d"].notna().all()
    assert result["relative_strength_vs_sector_20d"].notna().all()
    assert (result["sector_peer_count"] == 2).all()
    assert result["distance_from_52week_high"].notna().all()
    assert all(
        "distance_52week_insufficient_history" not in reasons
        for reasons in result["metric_quality_reasons"]
    )
    assert all(
        "stochastic_unavailable_missing_valid_ohlc" not in reasons
        for reasons in result["metric_quality_reasons"]
    )


def test_screen_assets_marks_stochastic_unavailable_without_valid_ohlc():
    sessions = _xtks_sessions("2026-04-01", "2026-06-30", 30)
    prices = pd.DataFrame(
        {
            "symbol": "13060",
            "price_time": sessions,
            "close": range(100, 130),
            "price_basis": "raw_ohlcv_with_adjusted",
        }
    )
    assets = pd.DataFrame(
        [
            {
                "symbol": "13060",
                "name": "ETF",
                "asset_type": "etf",
                "metadata_json": {},
            }
        ]
    )

    result = screen_assets(prices, assets)

    assert pd.isna(result.iloc[0]["stoch_raw_k_14"])
    assert pd.isna(result.iloc[0]["stoch_k_14_3"])
    assert pd.isna(result.iloc[0]["stoch_d_14_3_3"])
    assert "stochastic_unavailable_missing_valid_ohlc" in result.iloc[0][
        "metric_quality_reasons"
    ]
    assert "support_resistance_unavailable_missing_valid_ohlc" in result.iloc[0][
        "metric_quality_reasons"
    ]


def test_screen_assets_persists_nearest_active_support_and_resistance_without_score_change():
    sessions = _xtks_sessions("2026-01-01", "2026-03-31", 36)
    closes = [
        110, 112, 115, 112, 108, 105, 108, 112, 115, 112, 108, 105,
        108, 112, 115, 112, 108, 105, 108, 112, 115, 112, 108, 105,
        108, 112, 115, 112, 108, 105, 108, 112, 115, 112, 108, 110,
    ]
    prices = pd.DataFrame(
        {
            "symbol": "13060",
            "price_time": sessions,
            "close": closes,
            "high": [value + 2 for value in closes],
            "low": [value - 2 for value in closes],
            "price_basis": "raw_ohlcv_with_adjusted",
        }
    )
    assets = pd.DataFrame(
        [{"symbol": "13060", "name": "ETF", "asset_type": "etf", "metadata_json": {}}]
    )

    result = screen_assets(prices, assets)
    row = result.iloc[0]

    assert row["nearest_support_level"] == 103
    assert row["nearest_support_touch_count"] >= 2
    assert row["nearest_resistance_level"] == 117
    assert row["nearest_resistance_touch_count"] >= 2
    assert len(row["support_resistance_candidates"]) >= 2
    indicators = short_term_indicator_frame(
        prices.set_index("price_time")["close"],
        prices.set_index("price_time")["high"],
        prices.set_index("price_time")["low"],
    )
    assert row["attention_score"] == technical_attention_snapshot(
        indicators.iloc[-1], observations=36
    )["attention_score"]
