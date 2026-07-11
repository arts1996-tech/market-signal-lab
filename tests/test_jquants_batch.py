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
