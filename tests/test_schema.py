from app.database.models import CorrelationResult, MarketPrice
from app.database.repositories import chunked


def test_market_price_has_duplicate_prevention_constraint():
    constraints = {constraint.name for constraint in MarketPrice.__table__.constraints}

    assert "uq_market_price" in constraints


def test_correlation_result_has_duplicate_prevention_constraint():
    constraints = {constraint.name for constraint in CorrelationResult.__table__.constraints}

    assert "uq_correlation_result" in constraints


def test_chunked_splits_large_payloads():
    payload = [{"value": value} for value in range(2501)]

    chunks = list(chunked(payload, 1000))

    assert [len(chunk) for chunk in chunks] == [1000, 1000, 501]
