from pathlib import Path

import pandas as pd
from sqlalchemy.dialects import postgresql

from app.core.config import Settings
from app.core.data_source_policy import SOURCE_POLICY_VERSION
from app.database.repositories import (
    list_assets_with_minimum_price_history,
    market_prices_frame,
    resolve_market_price_sources,
)
from app.services.analysis_service import build_analysis_input_provenance, market_price_source_policy


class _Result:
    def mappings(self):
        return self

    def all(self):
        return []


class _CaptureSession:
    def __init__(self):
        self.query = None

    def execute(self, query):
        self.query = query
        return _Result()


def _compiled_sql(source_policy: str) -> str:
    session = _CaptureSession()
    market_prices_frame(session, ["NASDAQCOM"], source_policy=source_policy)
    return str(session.query.compile(dialect=postgresql.dialect()))


def test_market_data_mode_selects_one_source_scope():
    assert market_price_source_policy(Settings(market_data_mode="live")) == "real_only"
    assert market_price_source_policy(Settings(market_data_mode="demo")) == "demo_only"


def test_market_prices_frame_excludes_sample_by_default_and_demo_selects_only_sample():
    live_sql = _compiled_sql("real_only")
    demo_sql = _compiled_sql("demo_only")

    assert "market_prices.source !=" in live_sql
    assert "market_prices.source =" in demo_sql


def test_screening_asset_query_requires_distinct_adjusted_sessions():
    class _ScalarCaptureSession:
        query = None

        def scalars(self, query):
            self.query = query
            return []

    session = _ScalarCaptureSession()
    list_assets_with_minimum_price_history(
        session,
        source="jquants",
        asset_types=["stock", "etf"],
        min_history=30,
        limit=200,
        price_bases=["raw_ohlcv_with_adjusted"],
    )

    sql = str(session.query.compile(dialect=postgresql.dialect()))
    assert "count(distinct(market_prices.session_date))" in sql.lower()
    assert "market_prices.adjusted_close IS NOT NULL" in sql
    assert "market_prices.price_basis IN" in sql


def test_normal_compose_start_does_not_seed_synthetic_data():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    app_command = compose.split("jquants-collector:", maxsplit=1)[0]

    assert "python jobs/seed_sample_data.py" not in app_command


def test_real_source_policy_is_deterministic_and_rejects_unapproved_sources():
    prices = pd.DataFrame(
        [
            {
                "asset_id": "index", "symbol": "NASDAQCOM", "asset_type": "index", "currency": "USD",
                "timeframe": "1d", "price_time": "2024-01-02", "source": "fred", "source_symbol": "NASDAQCOM",
                "available_at": "2024-01-03", "fetched_at": "2024-01-03", "price_id": "a", "close": 100, "price_basis": "provider_reported_close_only",
            },
            {
                "asset_id": "index", "symbol": "NASDAQCOM", "asset_type": "index", "currency": "USD",
                "timeframe": "1d", "price_time": "2024-01-02", "source": "other", "source_symbol": "NASDAQCOM",
                "available_at": "2024-01-04", "fetched_at": "2024-01-04", "price_id": "z", "close": 999, "price_basis": "provider_reported_ohlcv",
            },
            {
                "asset_id": "jp-stock", "symbol": "13060", "asset_type": "etf", "currency": "JPY",
                "timeframe": "1d", "price_time": "2024-01-02", "source": "jquants", "source_symbol": "13060",
                "available_at": "2024-01-03", "fetched_at": "2024-01-03", "price_id": "b", "close": 200, "price_basis": "raw_ohlcv_with_adjusted",
            },
            {
                "asset_id": "us-stock", "symbol": "US001", "asset_type": "stock", "currency": "USD",
                "timeframe": "1d", "price_time": "2024-01-02", "source": "other", "source_symbol": "US001",
                "available_at": "2024-01-03", "fetched_at": "2024-01-03", "price_id": "c", "close": 300, "price_basis": "provider_reported_ohlcv",
            },
        ]
    )

    selected = resolve_market_price_sources(prices)

    assert selected.set_index("symbol")["close"].to_dict() == {"NASDAQCOM": 100, "13060": 200}


def test_real_source_policy_prefers_adjusted_values_but_keeps_raw_close():
    prices = pd.DataFrame([{
        "asset_id": "jp-stock", "symbol": "13060", "asset_type": "etf", "currency": "JPY",
        "timeframe": "1d", "price_time": "2024-01-02", "source": "jquants",
        "source_symbol": "13060", "available_at": "2024-01-03", "fetched_at": "2024-01-03",
        "price_id": "b", "open": 100, "high": 110, "low": 90, "close": 105,
        "adjusted_open": 50, "adjusted_high": 55, "adjusted_low": 45, "adjusted_close": 52.5,
        "volume": 1000, "adjusted_volume": 2000, "price_basis": "raw_ohlcv_with_adjusted",
    }])

    selected = resolve_market_price_sources(prices)

    assert selected.iloc[0]["close"] == 52.5
    assert selected.iloc[0]["raw_close"] == 105


def test_input_provenance_changes_when_selected_input_changes():
    prices = pd.DataFrame(
        {
            "symbol": ["NASDAQCOM"], "timeframe": ["1d"], "price_time": ["2024-01-02"],
            "source": ["fred"], "source_symbol": ["NASDAQCOM"], "open": [None], "high": [None],
            "low": [None], "close": [100], "adjusted_close": [None], "volume": [None],
            "data_quality_status": ["complete"], "price_basis": ["provider_reported_close_only"], "available_at": ["2024-01-03"],
        }
    )

    first = build_analysis_input_provenance(prices)
    prices.loc[0, "close"] = 101
    second = build_analysis_input_provenance(prices)

    assert first["source_policy_version"] == SOURCE_POLICY_VERSION
    assert first["input_data_version"] != second["input_data_version"]
