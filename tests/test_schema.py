from app.database.models import (
    AssetAnalysisResult,
    AssetAnalysisRun,
    AssetLifecycleRecord,
    AssetUniverseCoverage,
    CorrelationResult,
    CorporateAction,
    CorporateActionCoverage,
    MarketPrice,
    SpilloverFeature,
    SpilloverModelResult,
    VirtualAccount,
    VirtualAccountDailyState,
    VirtualAccountEvent,
    UserAssetSelectionAnalysisResult,
    UserAssetSelectionAnalysisRun,
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


def test_corporate_action_tables_have_event_and_coverage_idempotency_constraints():
    event_constraints = {
        constraint.name for constraint in CorporateAction.__table__.constraints
    }
    coverage_constraints = {
        constraint.name
        for constraint in CorporateActionCoverage.__table__.constraints
    }

    assert "uq_corporate_action_source_event" in event_constraints
    assert "ck_corporate_action_type" in event_constraints
    assert "ck_corporate_action_status" in event_constraints
    assert "ck_corporate_action_ratio" in event_constraints
    assert "ck_corporate_action_dividend_terms" in event_constraints
    assert "uq_corporate_action_coverage_period" in coverage_constraints
    assert "ck_corporate_action_coverage_period" in coverage_constraints
    assert "ck_corporate_action_coverage_status" in coverage_constraints
    assert {"ex_date", "record_date", "payable_date", "ratio", "cash_per_share"}.issubset(
        CorporateAction.__table__.columns.keys()
    )


def test_asset_lifecycle_tables_preserve_point_in_time_revisions_and_coverage():
    lifecycle_constraints = {
        constraint.name for constraint in AssetLifecycleRecord.__table__.constraints
    }
    coverage_constraints = {
        constraint.name for constraint in AssetUniverseCoverage.__table__.constraints
    }

    assert "uq_asset_lifecycle_revision" in lifecycle_constraints
    assert "ck_asset_lifecycle_effective_period" in lifecycle_constraints
    assert "ck_asset_lifecycle_listing_period" in lifecycle_constraints
    assert "ck_asset_lifecycle_status" in lifecycle_constraints
    assert "uq_asset_universe_coverage_revision" in coverage_constraints
    assert "ck_asset_universe_coverage_period" in coverage_constraints
    assert "ck_asset_universe_coverage_status" in coverage_constraints


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
    for column in ("equity", "unrealized_pnl", "cumulative_pnl", "maximum_drawdown"):
        assert VirtualAccountDailyState.__table__.columns[column].nullable


def test_asset_analysis_has_version_and_per_run_asset_constraints():
    run_constraints = {
        constraint.name for constraint in AssetAnalysisRun.__table__.constraints
    }
    result_constraints = {
        constraint.name for constraint in AssetAnalysisResult.__table__.constraints
    }

    assert "uq_asset_analysis_run_input" in run_constraints
    assert "uq_asset_analysis_result_asset" in result_constraints
    assert {
        "rule_version",
        "data_scope",
        "source_policy_version",
        "input_data_version",
        "assets_considered",
        "eligible_asset_count",
        "result_count",
    }.issubset(AssetAnalysisRun.__table__.columns.keys())


def test_user_selection_analysis_snapshots_are_idempotent_and_separate_from_accounts():
    run_constraints = {
        constraint.name for constraint in UserAssetSelectionAnalysisRun.__table__.constraints
    }
    result_constraints = {
        constraint.name
        for constraint in UserAssetSelectionAnalysisResult.__table__.constraints
    }

    assert "uq_user_selection_analysis_source" in run_constraints
    assert "uq_user_selection_analysis_result_asset" in result_constraints
    assert {
        "selection_id",
        "selection_key",
        "selection_version",
        "selection_composition_hash",
        "source_asset_analysis_run_id",
        "snapshot_hash",
        "input_data_version",
    }.issubset(UserAssetSelectionAnalysisRun.__table__.columns.keys())
    assert {"analysis_status", "quality_reasons", "input_hash", "result"}.issubset(
        UserAssetSelectionAnalysisResult.__table__.columns.keys()
    )


def test_chunked_splits_large_payloads():
    payload = [{"value": value} for value in range(2501)]

    chunks = list(chunked(payload, 1000))

    assert [len(chunk) for chunk in chunks] == [1000, 1000, 501]


def test_settings_include_jquants_defaults():
    settings = Settings()

    assert settings.jquants_base_url == "https://api.jquants.com"
    assert settings.jquants_min_request_interval_seconds == 15
    assert settings.data_stale_after_days == 7
