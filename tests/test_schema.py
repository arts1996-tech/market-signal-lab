from app.database.models import (
    CorrelationResult,
    MarketPrice,
    SpilloverFeature,
    SpilloverModelResult,
    VirtualAccount,
    VirtualAccountDailyState,
    VirtualAccountEvent,
)
from app.database.repositories import chunked
from app.core.config import Settings


def test_market_price_has_duplicate_prevention_constraint():
    constraints = {constraint.name for constraint in MarketPrice.__table__.constraints}

    assert "uq_market_price" in constraints
    assert {
        "session_date",
        "source_symbol",
        "available_at",
        "data_quality_status",
        "price_basis",
        "adjusted_open",
        "adjusted_high",
        "adjusted_low",
        "adjusted_volume",
        "adjustment_factor",
    }.issubset(MarketPrice.__table__.columns.keys())


def test_correlation_result_has_duplicate_prevention_constraint():
    constraints = {constraint.name for constraint in CorrelationResult.__table__.constraints}

    assert "uq_correlation_result_input" in constraints


def test_spillover_feature_has_duplicate_prevention_constraint():
    constraints = {constraint.name for constraint in SpilloverFeature.__table__.constraints}

    assert "uq_spillover_feature_input" in constraints


def test_spillover_model_result_has_duplicate_prevention_constraint():
    constraints = {constraint.name for constraint in SpilloverModelResult.__table__.constraints}

    assert "uq_spillover_model_result_input" in constraints


def test_virtual_account_ledger_has_freeze_and_event_idempotency_constraints():
    account_constraints = {
        constraint.name for constraint in VirtualAccount.__table__.constraints
    }
    state_constraints = {
        constraint.name for constraint in VirtualAccountDailyState.__table__.constraints
    }
    event_constraints = {
        constraint.name for constraint in VirtualAccountEvent.__table__.constraints
    }

    assert "uq_virtual_accounts_account_name" in account_constraints
    assert "uq_virtual_account_daily_state_track_session" in state_constraints
    assert "uq_virtual_account_event_id" in event_constraints
    assert {
        "decision_track",
        "price_latest_session",
        "data_delay_days",
        "data_sources",
        "quality_gate_status",
        "quality_gate_reasons",
        "observation_input_hash",
    }.issubset(VirtualAccountDailyState.__table__.columns.keys())
    assert "decision_track" in VirtualAccountEvent.__table__.columns


def test_chunked_splits_large_payloads():
    payload = [{"value": value} for value in range(2501)]

    chunks = list(chunked(payload, 1000))

    assert [len(chunk) for chunk in chunks] == [1000, 1000, 501]


def test_settings_include_jquants_defaults():
    settings = Settings()

    assert settings.jquants_base_url == "https://api.jquants.com"
    assert settings.jquants_min_request_interval_seconds == 15
    assert settings.data_stale_after_days == 7
