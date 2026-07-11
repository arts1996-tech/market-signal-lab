from app.database.models import MarketPrice


def test_market_price_has_duplicate_prevention_constraint():
    constraints = {constraint.name for constraint in MarketPrice.__table__.constraints}

    assert "uq_market_price" in constraints

