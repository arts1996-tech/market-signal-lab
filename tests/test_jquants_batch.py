from datetime import UTC, datetime

import pandas as pd

from app.services import market_service


class FakeJQuantsClient:
    sleeps = 0

    def respect_free_plan_rate_limit(self):
        FakeJQuantsClient.sleeps += 1


def test_collect_jquants_daily_batch_uses_codes_and_rate_limit(monkeypatch):
    calls = []
    FakeJQuantsClient.sleeps = 0

    def fake_collect_daily(session, code, date, name=None):
        calls.append({"code": code, "date": date, "name": name})
        return {"status": "success", "saved_rows": 1}

    monkeypatch.setattr(market_service, "JQuantsClient", FakeJQuantsClient)
    monkeypatch.setattr(market_service, "collect_jquants_daily_bars", fake_collect_daily)

    result = market_service.collect_jquants_daily_batch(
        session=object(),
        date="20260401",
        codes=["86970", "72030", "67580"],
    )

    assert [call["code"] for call in calls] == ["86970", "72030", "67580"]
    assert result["requested"] == 3
    assert result["success"] == 3
    assert FakeJQuantsClient.sleeps == 2


def test_collect_jquants_daily_batch_honors_limit(monkeypatch):
    calls = []
    FakeJQuantsClient.sleeps = 0

    def fake_collect_daily(session, code, date, name=None):
        calls.append(code)
        return {"status": "success", "saved_rows": 1}

    monkeypatch.setattr(market_service, "JQuantsClient", FakeJQuantsClient)
    monkeypatch.setattr(market_service, "collect_jquants_daily_bars", fake_collect_daily)

    result = market_service.collect_jquants_daily_batch(
        session=object(),
        date="20260401",
        codes=["86970", "72030", "67580"],
        limit=2,
    )

    assert calls == ["86970", "72030"]
    assert result["requested"] == 2
    assert FakeJQuantsClient.sleeps == 1


def test_collect_jquants_daily_batch_keeps_rate_limit_failures_retryable(monkeypatch):
    monkeypatch.setattr(market_service, "JQuantsClient", FakeJQuantsClient)
    monkeypatch.setattr(
        market_service,
        "collect_jquants_daily_bars",
        lambda *args, **kwargs: {"status": "retry_pending", "error_category": "rate_limited"},
    )

    result = market_service.collect_jquants_daily_batch(
        session=object(), date="20260401", codes=["86970"]
    )

    assert result["status"] == "retry_pending"
    assert result["retry_pending"] == 1
    assert result["no_data"] == 0


def test_collect_jquants_daily_bars_persists_raw_and_adjusted_price_basis(monkeypatch):
    class FakeSession:
        def commit(self):
            pass

        def rollback(self):
            pass

    class FakeClient:
        def fetch_daily_bars(self, code, **kwargs):
            return (
                pd.DataFrame(
                    [
                        {
                            "price_time": datetime(2026, 4, 10, tzinfo=UTC),
                            "session_date": datetime(2026, 4, 10, tzinfo=UTC).date(),
                            "open": 100,
                            "high": 110,
                            "low": 90,
                            "close": 105,
                            "adjusted_open": 50,
                            "adjusted_high": 55,
                            "adjusted_low": 45,
                            "adjusted_close": 52.5,
                            "adjusted_volume": 2_000,
                            "adjustment_factor": 0.5,
                            "volume": 1_000,
                            "source": "jquants",
                            "source_symbol": "86970",
                            "fetched_at": datetime(2026, 7, 12, tzinfo=UTC),
                            "available_at": datetime(2026, 7, 12, tzinfo=UTC),
                            "data_quality_status": "complete_ohlcv",
                            "price_basis": "raw_ohlcv_with_adjusted",
                        }
                    ]
                ),
                10,
            )

    captured = []
    monkeypatch.setattr(market_service, "JQuantsClient", FakeClient)
    monkeypatch.setattr(market_service, "upsert_assets", lambda *_: {"86970": type("Asset", (), {"id": "asset-id"})()})
    monkeypatch.setattr(market_service, "upsert_market_prices", lambda _session, rows: captured.extend(rows) or len(captured))
    monkeypatch.setattr(market_service, "insert_api_fetch_log", lambda *args, **kwargs: None)

    result = market_service.collect_jquants_daily_bars(FakeSession(), code="86970", date="20260410")

    assert result["status"] == "success"
    assert captured == [
        {
            **captured[0],
            "adjusted_open": 50,
            "adjusted_high": 55,
            "adjusted_low": 45,
            "adjusted_close": 52.5,
            "adjusted_volume": 2_000,
            "adjustment_factor": 0.5,
            "price_basis": "raw_ohlcv_with_adjusted",
        }
    ]
