from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.analysis.trade_modes import TradeMode
from app.backtest.audit import stable_payload_hash
from app.backtest.simulation_review import (
    CompletedTradeOutcome,
    FactorAssessment,
    FrozenDecisionSnapshot,
    NonExecutionOutcome,
    OutcomeFactorObservation,
    ReviewBenchmark,
    ReviewCostBreakdown,
    ReviewPriceObservation,
    ReviewStatus,
    ReviewSubject,
    review_completed_trade,
    review_non_execution,
)


DECISION_AT = datetime(2026, 1, 5, 6, tzinfo=timezone.utc)
ENTRY_AT = DECISION_AT + timedelta(days=1)
EXIT_AT = ENTRY_AT + timedelta(days=2)
REVIEWED_AT = EXIT_AT + timedelta(hours=6)


def _hash(name: str) -> str:
    return stable_payload_hash({"fixture": name})


def _decision(
    mode: TradeMode = TradeMode.CASH,
    *,
    decision_mode: TradeMode | None = None,
) -> FrozenDecisionSnapshot:
    return FrozenDecisionSnapshot(
        decision_id="decision-1",
        source_reference_type="trade_mode_backtest",
        source_reference_id="run-1",
        asset_id="asset-1",
        symbol="1306",
        horizon="short_term",
        decision_mode=decision_mode or mode,
        execution_mode=mode,
        decision_at=DECISION_AT,
        data_as_of=DECISION_AT - timedelta(hours=2),
        available_at=DECISION_AT - timedelta(hours=1),
        data_scope="synthetic_research",
        strategy_version="strategy-v1",
        rule_version="rule-v1",
        execution_version="execution-v1",
        cost_version="cost-v1",
        reason_codes=("trend_up",),
        counterargument_codes=("event_risk",),
        invalidation_conditions=("support_break",),
        quality_warnings=("synthetic_input",),
        input_hash=_hash("decision"),
    )


def _observation(
    start: datetime,
    *,
    high: float,
    low: float,
    close: float,
    name: str,
) -> ReviewPriceObservation:
    end = start + timedelta(hours=6)
    return ReviewPriceObservation(
        period_start_at=start,
        period_end_at=end,
        available_at=end + timedelta(minutes=1),
        high=high,
        low=low,
        close=close,
        fx_rate_to_account=1,
        input_hash=_hash(name),
    )


def _trade(**changes) -> CompletedTradeOutcome:
    observations = (
        _observation(ENTRY_AT, high=110, low=95, close=105, name="day-1"),
        _observation(
            ENTRY_AT + timedelta(days=1),
            high=120,
            low=90,
            close=115,
            name="day-2",
        ),
    )
    values = {
        "outcome_id": "outcome-1",
        "entry_at": ENTRY_AT,
        "exit_at": EXIT_AT,
        "available_at": EXIT_AT + timedelta(hours=1),
        "entry_price": 100,
        "exit_price": 115,
        "entry_fx_rate_to_account": 1,
        "exit_fx_rate_to_account": 1,
        "quantity": 100,
        "holding_sessions": 2,
        "exit_reason": "take_profit",
        "deducted_costs": ReviewCostBreakdown(fees=100, financing=50),
        "embedded_costs": ReviewCostBreakdown(spread=20),
        "position_cashflow_pnl": 0,
        "reported_net_pnl": 1_350,
        "account_maximum_drawdown": -0.02,
        "price_observations": observations,
        "price_path_complete": True,
        "price_path_coverage_hash": _hash("price-path"),
        "event_ids": ("entry-event", "exit-event"),
        "input_hash": _hash("outcome"),
    }
    values.update(changes)
    return CompletedTradeOutcome(**values)


def _benchmark(start=ENTRY_AT, end=EXIT_AT) -> ReviewBenchmark:
    return ReviewBenchmark(
        symbol="TOPIX",
        period_start_at=start,
        period_end_at=end,
        available_at=end + timedelta(hours=1),
        start_value=100,
        end_value=105,
        input_hash=_hash("benchmark"),
    )


def _factor(
    code: str,
    assessment: FactorAssessment,
    *,
    observed_at: datetime,
) -> OutcomeFactorObservation:
    return OutcomeFactorObservation(
        factor_code=code,
        assessment=assessment,
        observed_at=observed_at,
        available_at=observed_at + timedelta(minutes=1),
        source="reviewed_fixture",
        input_hash=_hash(f"factor-{code}"),
    )


def test_long_trade_review_reconciles_costs_excursions_benchmark_and_factors():
    factors = (
        _factor("event_risk", FactorAssessment.ADVERSE, observed_at=EXIT_AT),
        _factor("trend_up", FactorAssessment.SUPPORTIVE, observed_at=ENTRY_AT),
        _factor(
            "surprise_policy",
            FactorAssessment.UNOBSERVED_AT_DECISION,
            observed_at=EXIT_AT,
        ),
    )

    result = review_completed_trade(
        _decision(),
        _trade(),
        reviewed_at=REVIEWED_AT,
        benchmark=_benchmark(),
        factors=factors,
    )

    assert result.status == ReviewStatus.COMPLETE
    assert result.gross_pnl == 1_500
    assert result.net_pnl == 1_350
    assert result.net_return == pytest.approx(0.135)
    assert result.total_deducted_cost == 150
    assert result.total_embedded_cost == 20
    assert result.maximum_favorable_excursion == 2_000
    assert result.maximum_favorable_excursion_rate == pytest.approx(0.20)
    assert result.maximum_adverse_excursion == -1_000
    assert result.maximum_adverse_excursion_rate == pytest.approx(-0.10)
    assert result.position_path_maximum_drawdown == pytest.approx(-0.25)
    assert result.benchmark_return == pytest.approx(0.05)
    assert result.excess_return == pytest.approx(0.085)
    assert result.confirmed_reason_codes == ("trend_up",)
    assert result.materialized_counterargument_codes == ("event_risk",)
    assert result.unobserved_factor_codes == ("surprise_policy",)
    assert result.included_in_performance
    assert not result.real_order_sent


def test_short_trade_uses_inverse_excursion_direction():
    outcome = _trade(
        exit_price=90,
        reported_net_pnl=850,
        price_observations=(
            _observation(ENTRY_AT, high=115, low=80, close=90, name="short-day"),
        ),
    )

    result = review_completed_trade(
        _decision(TradeMode.MARGIN_SHORT),
        outcome,
        reviewed_at=REVIEWED_AT,
    )

    assert result.gross_pnl == 1_000
    assert result.net_pnl == 850
    assert result.maximum_favorable_excursion == 2_000
    assert result.maximum_adverse_excursion == -1_500
    assert result.maximum_favorable_excursion_rate == pytest.approx(0.20)
    assert result.maximum_adverse_excursion_rate == pytest.approx(-0.15)


def test_unverified_holding_path_keeps_pnl_but_withholds_excursion_claims():
    outcome = _trade(
        price_observations=(),
        price_path_complete=False,
        price_path_coverage_hash=None,
    )

    result = review_completed_trade(
        _decision(),
        outcome,
        reviewed_at=REVIEWED_AT,
    )

    assert result.status == ReviewStatus.INSUFFICIENT_DATA
    assert result.status_reasons == ("holding_period_price_path_unverified",)
    assert result.net_pnl == 1_350
    assert result.maximum_favorable_excursion is None
    assert result.maximum_adverse_excursion is None


def test_skip_counterfactual_is_explicitly_excluded_from_performance():
    outcome = NonExecutionOutcome(
        outcome_id="skip-1",
        subject=ReviewSubject.SKIPPED,
        outcome_at=EXIT_AT,
        available_at=EXIT_AT + timedelta(hours=1),
        reason_codes=("risk_reward_below_minimum",),
        reference_price=100,
        reference_fx_rate_to_account=1,
        observed_price=110,
        observed_fx_rate_to_account=1,
        input_hash=_hash("skip"),
    )

    result = review_non_execution(
        _decision(),
        outcome,
        reviewed_at=REVIEWED_AT,
        benchmark=_benchmark(start=DECISION_AT),
    )

    assert result.subject == ReviewSubject.SKIPPED
    assert result.observed_asset_return_if_no_execution == pytest.approx(0.10)
    assert result.observed_asset_excess_return_if_no_execution == pytest.approx(0.05)
    assert result.net_pnl is None
    assert result.excess_return is None
    assert not result.included_in_performance
    assert "counterfactual_observation_excluded_from_performance" in (
        result.quality_warnings
    )


def test_non_execution_without_price_is_saved_as_insufficient_not_zero_return():
    outcome = NonExecutionOutcome(
        outcome_id="unfilled-1",
        subject=ReviewSubject.UNFILLED,
        outcome_at=EXIT_AT,
        available_at=EXIT_AT + timedelta(hours=1),
        reason_codes=("limit_not_reached",),
        input_hash=_hash("unfilled"),
    )

    result = review_non_execution(
        _decision(),
        outcome,
        reviewed_at=REVIEWED_AT,
    )

    assert result.status == ReviewStatus.INSUFFICIENT_DATA
    assert result.observed_asset_return_if_no_execution is None
    assert "counterfactual_price_observation_missing" in result.status_reasons


def test_review_rejects_future_evidence_and_mismatched_benchmark_period():
    future_factor = _factor(
        "late_news",
        FactorAssessment.UNOBSERVED_AT_DECISION,
        observed_at=REVIEWED_AT + timedelta(hours=1),
    )
    with pytest.raises(ValueError, match="not available"):
        review_completed_trade(
            _decision(),
            _trade(),
            reviewed_at=REVIEWED_AT,
            factors=(future_factor,),
        )

    with pytest.raises(ValueError, match="exactly match"):
        review_completed_trade(
            _decision(),
            _trade(),
            reviewed_at=REVIEWED_AT,
            benchmark=_benchmark(start=DECISION_AT),
        )


def test_review_rejects_non_reconciling_pnl_and_post_exit_price_interval():
    with pytest.raises(ValueError, match="does not reconcile"):
        review_completed_trade(
            _decision(),
            _trade(reported_net_pnl=999),
            reviewed_at=REVIEWED_AT,
        )

    with pytest.raises(ValidationError, match="holding period"):
        _trade(
            price_observations=(
                _observation(
                    EXIT_AT,
                    high=120,
                    low=100,
                    close=110,
                    name="after-exit",
                ),
            )
        )


def test_review_hash_is_stable_for_reordered_factor_input():
    first_factor = _factor(
        "trend_up",
        FactorAssessment.SUPPORTIVE,
        observed_at=ENTRY_AT,
    )
    second_factor = _factor(
        "event_risk",
        FactorAssessment.ADVERSE,
        observed_at=EXIT_AT,
    )
    first = review_completed_trade(
        _decision(),
        _trade(),
        reviewed_at=REVIEWED_AT,
        factors=(first_factor, second_factor),
    )
    reordered = review_completed_trade(
        _decision(),
        _trade(),
        reviewed_at=REVIEWED_AT,
        factors=(second_factor, first_factor),
    )

    assert first.review_id == reordered.review_id
    assert first.review_input_hash == reordered.review_input_hash


def test_decision_snapshot_rejects_current_market_and_future_available_input():
    values = _decision().model_dump()
    values["data_scope"] = "current_market"
    with pytest.raises(ValidationError):
        FrozenDecisionSnapshot(**values)

    values = _decision().model_dump()
    values["available_at"] = DECISION_AT + timedelta(seconds=1)
    with pytest.raises(ValidationError, match="available"):
        FrozenDecisionSnapshot(**values)
