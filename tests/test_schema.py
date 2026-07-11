from app.database.models import CorrelationResult, MarketPrice


def test_market_price_has_duplicate_prevention_constraint():
    constraints = {constraint.name for constraint in MarketPrice.__table__.constraints}

    assert "uq_market_price" in constraints


def test_correlation_result_has_duplicate_prevention_constraint():
    constraints = {constraint.name for constraint in CorrelationResult.__table__.constraints}

    assert "uq_correlation_result" in constraints
