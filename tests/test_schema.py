from app.database.models import (
    AssetAnalysisResult,
    AssetAnalysisRun,
    AssetLifecycleRecord,
    AssetTradingCapability,
    AssetUniverseCoverage,
    CorrelationResult,
    CorporateAction,
    CorporateActionCoverage,
    FinancingTermSnapshot,
    MarginMarketSnapshot,
    MarketPrice,
    SpilloverFeature,
    SpilloverModelResult,
    VirtualAccount,
    VirtualAccountDailyState,
    VirtualAccountEvent,
    UserAssetSelectionAnalysisResult,
    UserAssetSelectionAnalysisRun,
    SelectedUniverseBacktestAssetResult,
    SelectedUniverseBacktestRun,
    SelectedUniverseForwardActivationEvent,
    SelectedUniverseValidationClaim,
    SimulationReview,
    TradeModeBacktestRun,
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
    assert "ck_virtual_accounts_scope" in account_constraints
    assert "ck_virtual_accounts_selection_scope" in account_constraints
    assert {
        "account_scope",
        "allowed_selection_id",
        "allowed_selection_version",
        "allowed_selection_composition_hash",
        "selection_change_policy",
    }.issubset(VirtualAccount.__table__.columns.keys())
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


def test_selected_forward_activation_events_are_explicit_and_idempotent():
    constraints = {
        constraint.name
        for constraint in SelectedUniverseForwardActivationEvent.__table__.constraints
    }

    assert "uq_selected_forward_activation_request" in constraints
    assert {
        "selection_id",
        "enabled",
        "request_id",
        "requested_at",
        "requested_by",
        "activation_version",
        "input_hash",
    }.issubset(SelectedUniverseForwardActivationEvent.__table__.columns.keys())


def test_margin_snapshots_are_separate_append_only_ready_histories():
    capability_constraints = {
        constraint.name for constraint in AssetTradingCapability.__table__.constraints
    }
    market_constraints = {
        constraint.name for constraint in MarginMarketSnapshot.__table__.constraints
    }
    financing_constraints = {
        constraint.name for constraint in FinancingTermSnapshot.__table__.constraints
    }

    assert "uq_asset_trading_capability_provider_record" in capability_constraints
    assert "ck_asset_trading_capability_effective_period" in capability_constraints
    assert "uq_margin_market_snapshot_provider_record" in market_constraints
    assert "ck_margin_market_snapshot_long_balance" in market_constraints
    assert "uq_financing_term_snapshot_provider_record" in financing_constraints
    assert "ck_financing_term_snapshot_effective_period" in financing_constraints
    assert {
        "margin_long_eligible",
        "margin_short_eligible",
        "credit_types",
        "short_availability",
        "restriction_codes",
        "effective_from",
        "available_at",
        "fetched_at",
        "input_hash",
    }.issubset(AssetTradingCapability.__table__.columns.keys())
    assert {
        "margin_long_balance",
        "margin_short_balance",
        "lending_ratio",
        "reverse_stock_borrow_fee",
    }.issubset(MarginMarketSnapshot.__table__.columns.keys())
    assert {
        "margin_interest_rate",
        "stock_lending_fee",
        "borrow_cost",
        "initial_margin_rate",
        "maintenance_margin_rate",
        "minimum_margin_amount",
        "repayment_term_days",
    }.issubset(FinancingTermSnapshot.__table__.columns.keys())


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


def test_selected_universe_backtests_are_cash_only_and_separate_from_virtual_accounts():
    run_constraints = {
        constraint.name for constraint in SelectedUniverseBacktestRun.__table__.constraints
    }
    result_constraints = {
        constraint.name
        for constraint in SelectedUniverseBacktestAssetResult.__table__.constraints
    }

    assert "uq_selected_universe_backtest_input" in run_constraints
    assert "ck_selected_universe_backtest_cash_only" in run_constraints
    assert "uq_selected_universe_backtest_asset" in result_constraints
    assert {
        "analysis_snapshot_run_id",
        "selection_id",
        "selection_version",
        "scope",
        "trade_mode",
        "initial_cash",
        "simulation_hash",
        "result",
    }.issubset(SelectedUniverseBacktestRun.__table__.columns.keys())


def test_selected_universe_validation_claims_preserve_period_and_classification():
    constraints = {
        constraint.name for constraint in SelectedUniverseValidationClaim.__table__.constraints
    }

    assert "uq_selected_universe_validation_claim" in constraints
    assert "ck_selected_universe_validation_period" in constraints
    assert "ck_selected_universe_validation_classification" in constraints
    assert {"validation_claim_id", "evaluation_classification"}.issubset(
        SelectedUniverseBacktestRun.__table__.columns.keys()
    )


def test_trade_mode_backtest_runs_are_append_only_research_series():
    constraints = {
        constraint.name for constraint in TradeModeBacktestRun.__table__.constraints
    }

    assert "uq_trade_mode_backtest_run_id" in constraints
    assert "ck_trade_mode_backtest_mode" in constraints
    assert "ck_trade_mode_backtest_status" in constraints
    assert "ck_trade_mode_backtest_data_scope" in constraints
    assert "ck_trade_mode_backtest_research_only" in constraints
    assert {
        "run_id",
        "horizon",
        "trade_mode",
        "initial_cash",
        "input_hash",
        "result",
    }.issubset(TradeModeBacktestRun.__table__.columns.keys())


def test_simulation_reviews_preserve_decision_links_and_performance_scope():
    constraints = {
        constraint.name for constraint in SimulationReview.__table__.constraints
    }

    assert "uq_simulation_review_id" in constraints
    assert "ck_simulation_review_subject" in constraints
    assert "ck_simulation_review_status" in constraints
    assert "ck_simulation_review_horizon" in constraints
    assert "ck_simulation_review_data_scope" in constraints
    assert "ck_simulation_review_performance_scope" in constraints
    assert "ck_simulation_review_research_only" in constraints
    assert "ck_simulation_review_time_order" in constraints
    assert {
        "decision_id",
        "source_reference_type",
        "source_reference_id",
        "subject",
        "decision_input_hash",
        "outcome_input_hash",
        "review_input_hash",
        "result_hash",
        "included_in_performance",
        "result",
    }.issubset(SimulationReview.__table__.columns.keys())


def test_chunked_splits_large_payloads():
    payload = [{"value": value} for value in range(2501)]

    chunks = list(chunked(payload, 1000))

    assert [len(chunk) for chunk in chunks] == [1000, 1000, 501]


def test_settings_include_jquants_defaults():
    settings = Settings()

    assert settings.jquants_base_url == "https://api.jquants.com"
    assert settings.jquants_min_request_interval_seconds == 15
    assert settings.data_stale_after_days == 7
