"""Deterministic post-decision reviews without rewriting frozen decisions."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.analysis.trade_modes import TradeMode
from app.backtest.audit import stable_payload_hash


SIMULATION_REVIEW_VERSION = "simulation-review-v1"


class ReviewSubject(StrEnum):
    EXECUTED_TRADE = "executed_trade"
    SKIPPED = "skipped"
    UNFILLED = "unfilled"


class ReviewStatus(StrEnum):
    COMPLETE = "complete"
    INSUFFICIENT_DATA = "insufficient_data"


class FactorAssessment(StrEnum):
    SUPPORTIVE = "supportive"
    ADVERSE = "adverse"
    NEUTRAL = "neutral"
    UNOBSERVED_AT_DECISION = "unobserved_at_decision"


class FrozenDecisionSnapshot(BaseModel):
    """The exact decision-time evidence; review code never mutates this object."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: str = Field(min_length=1, max_length=128)
    source_reference_type: str = Field(min_length=1, max_length=64)
    source_reference_id: str = Field(min_length=1, max_length=128)
    asset_id: str = Field(min_length=1, max_length=64)
    symbol: str = Field(pattern=r"^[A-Z0-9.\-]{1,32}$")
    horizon: Literal["short_term", "mid_term"]
    decision_mode: TradeMode
    execution_mode: TradeMode | None = None
    decision_at: datetime
    data_as_of: datetime
    available_at: datetime
    data_scope: Literal["synthetic_research", "delayed_historical"]
    strategy_version: str = Field(min_length=1, max_length=64)
    rule_version: str = Field(min_length=1, max_length=64)
    execution_version: str = Field(min_length=1, max_length=64)
    cost_version: str = Field(min_length=1, max_length=64)
    reason_codes: tuple[str, ...] = ()
    counterargument_codes: tuple[str, ...] = ()
    invalidation_conditions: tuple[str, ...] = ()
    quality_warnings: tuple[str, ...] = ()
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    real_order_sent: Literal[False] = False

    @model_validator(mode="after")
    def validate_snapshot(self):
        times = (self.decision_at, self.data_as_of, self.available_at)
        if any(value.tzinfo is None or value.utcoffset() is None for value in times):
            raise ValueError("decision timestamps must be timezone-aware")
        if self.data_as_of > self.decision_at:
            raise ValueError("decision data cannot be after decision_at")
        if self.available_at > self.decision_at:
            raise ValueError("decision evidence must be available by decision_at")
        executable = {
            TradeMode.CASH,
            TradeMode.MARGIN_LONG,
            TradeMode.MARGIN_SHORT,
        }
        if self.execution_mode is not None and self.execution_mode not in executable:
            raise ValueError("execution_mode must be an executable mode")
        if self.decision_mode != TradeMode.AUTO_SELECT and self.execution_mode not in {
            None,
            self.decision_mode,
        }:
            raise ValueError("execution_mode must match a non-auto decision mode")
        for values in (
            self.reason_codes,
            self.counterargument_codes,
            self.invalidation_conditions,
            self.quality_warnings,
        ):
            if len(values) != len(set(values)):
                raise ValueError("decision evidence lists must not contain duplicates")
        return self


class ReviewCostBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fees: float = Field(default=0, ge=0)
    spread: float = Field(default=0, ge=0)
    slippage: float = Field(default=0, ge=0)
    fx_conversion: float = Field(default=0, ge=0)
    financing: float = Field(default=0, ge=0)
    taxes: float = Field(default=0, ge=0)
    other: float = Field(default=0, ge=0)

    def total(self) -> float:
        return sum(
            (
                self.fees,
                self.spread,
                self.slippage,
                self.fx_conversion,
                self.financing,
                self.taxes,
                self.other,
            )
        )


class ReviewPriceObservation(BaseModel):
    """A completed interval wholly inside the holding period."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    period_start_at: datetime
    period_end_at: datetime
    available_at: datetime
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    fx_rate_to_account: float = Field(gt=0)
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_observation(self):
        times = (self.period_start_at, self.period_end_at, self.available_at)
        if any(value.tzinfo is None or value.utcoffset() is None for value in times):
            raise ValueError("price observation timestamps must be timezone-aware")
        if self.period_end_at < self.period_start_at:
            raise ValueError("price observation end cannot precede its start")
        if self.available_at < self.period_end_at:
            raise ValueError("price observation cannot be available before interval end")
        if self.low > self.close or self.high < self.close or self.high < self.low:
            raise ValueError("price observation OHLC range is invalid")
        return self


class CompletedTradeOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome_id: str = Field(min_length=1, max_length=128)
    entry_at: datetime
    exit_at: datetime
    available_at: datetime
    entry_price: float = Field(gt=0)
    exit_price: float = Field(gt=0)
    entry_fx_rate_to_account: float = Field(gt=0)
    exit_fx_rate_to_account: float = Field(gt=0)
    quantity: int = Field(gt=0)
    holding_sessions: int = Field(ge=1)
    exit_reason: str = Field(min_length=1, max_length=128)
    deducted_costs: ReviewCostBreakdown = Field(default_factory=ReviewCostBreakdown)
    embedded_costs: ReviewCostBreakdown = Field(default_factory=ReviewCostBreakdown)
    position_cashflow_pnl: float = 0
    reported_net_pnl: float | None = None
    account_maximum_drawdown: float | None = Field(default=None, le=0)
    price_observations: tuple[ReviewPriceObservation, ...] = ()
    price_path_complete: bool = False
    price_path_coverage_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    event_ids: tuple[str, ...] = ()
    quality_warnings: tuple[str, ...] = ()
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    real_order_sent: Literal[False] = False

    @model_validator(mode="after")
    def validate_outcome(self):
        times = (self.entry_at, self.exit_at, self.available_at)
        if any(value.tzinfo is None or value.utcoffset() is None for value in times):
            raise ValueError("trade outcome timestamps must be timezone-aware")
        if self.exit_at < self.entry_at:
            raise ValueError("trade exit cannot precede entry")
        if self.available_at < self.exit_at:
            raise ValueError("trade outcome cannot be available before exit")
        if len(self.event_ids) != len(set(self.event_ids)):
            raise ValueError("trade event_ids must not contain duplicates")
        if len(self.quality_warnings) != len(set(self.quality_warnings)):
            raise ValueError("trade quality warnings must not contain duplicates")
        keys = [
            (item.period_start_at.astimezone(timezone.utc), item.period_end_at)
            for item in self.price_observations
        ]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("price observations must be unique and chronological")
        for item in self.price_observations:
            if item.period_start_at < self.entry_at or item.period_end_at > self.exit_at:
                raise ValueError("price observations must remain inside the holding period")
        if self.price_path_complete and (
            not self.price_observations or self.price_path_coverage_hash is None
        ):
            raise ValueError("complete price path requires observations and coverage hash")
        if not self.price_path_complete and self.price_path_coverage_hash is not None:
            raise ValueError("unverified price path cannot have a coverage hash")
        return self


class NonExecutionOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome_id: str = Field(min_length=1, max_length=128)
    subject: Literal[ReviewSubject.SKIPPED, ReviewSubject.UNFILLED]
    outcome_at: datetime
    available_at: datetime
    reason_codes: tuple[str, ...]
    reference_price: float | None = Field(default=None, gt=0)
    reference_fx_rate_to_account: float | None = Field(default=None, gt=0)
    observed_price: float | None = Field(default=None, gt=0)
    observed_fx_rate_to_account: float | None = Field(default=None, gt=0)
    quality_warnings: tuple[str, ...] = ()
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    real_order_sent: Literal[False] = False

    @model_validator(mode="after")
    def validate_outcome(self):
        for value in (self.outcome_at, self.available_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("non-execution timestamps must be timezone-aware")
        if self.available_at < self.outcome_at:
            raise ValueError("non-execution outcome cannot be available before outcome_at")
        if not self.reason_codes:
            raise ValueError("non-execution outcome requires reason codes")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("non-execution reason codes must not contain duplicates")
        price_values = (
            self.reference_price,
            self.reference_fx_rate_to_account,
            self.observed_price,
            self.observed_fx_rate_to_account,
        )
        if any(value is None for value in price_values) and not all(
            value is None for value in price_values
        ):
            raise ValueError("counterfactual observation values must be complete or absent")
        return self


class ReviewBenchmark(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str = Field(pattern=r"^[A-Z0-9.\-]{1,32}$")
    period_start_at: datetime
    period_end_at: datetime
    available_at: datetime
    start_value: float = Field(gt=0)
    end_value: float = Field(gt=0)
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_benchmark(self):
        times = (self.period_start_at, self.period_end_at, self.available_at)
        if any(value.tzinfo is None or value.utcoffset() is None for value in times):
            raise ValueError("benchmark timestamps must be timezone-aware")
        if self.period_end_at < self.period_start_at:
            raise ValueError("benchmark end cannot precede start")
        if self.available_at < self.period_end_at:
            raise ValueError("benchmark cannot be available before period end")
        return self


class OutcomeFactorObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    factor_code: str = Field(min_length=1, max_length=128)
    assessment: FactorAssessment
    observed_at: datetime
    available_at: datetime
    source: str = Field(min_length=1, max_length=128)
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_factor(self):
        for value in (self.observed_at, self.available_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("factor timestamps must be timezone-aware")
        if self.available_at < self.observed_at:
            raise ValueError("factor cannot be available before observation")
        return self


class SimulationReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    review_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_version: str = SIMULATION_REVIEW_VERSION
    reviewed_at: datetime
    subject: ReviewSubject
    status: ReviewStatus
    status_reasons: tuple[str, ...]
    decision: FrozenDecisionSnapshot
    outcome: CompletedTradeOutcome | NonExecutionOutcome
    benchmark: ReviewBenchmark | None
    factor_observations: tuple[OutcomeFactorObservation, ...]
    outcome_id: str
    outcome_at: datetime
    outcome_input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    benchmark_input_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    factor_input_hashes: tuple[str, ...]
    gross_pnl: float | None
    net_pnl: float | None
    net_return: float | None
    total_deducted_cost: float | None
    total_embedded_cost: float | None
    position_cashflow_pnl: float | None
    maximum_favorable_excursion: float | None
    maximum_favorable_excursion_rate: float | None
    maximum_adverse_excursion: float | None
    maximum_adverse_excursion_rate: float | None
    position_path_maximum_drawdown: float | None
    account_maximum_drawdown: float | None
    holding_sessions: int | None
    benchmark_symbol: str | None
    benchmark_return: float | None
    excess_return: float | None
    observed_asset_return_if_no_execution: float | None
    observed_asset_excess_return_if_no_execution: float | None
    included_in_performance: bool
    confirmed_reason_codes: tuple[str, ...]
    materialized_counterargument_codes: tuple[str, ...]
    unobserved_factor_codes: tuple[str, ...]
    neutral_factor_codes: tuple[str, ...]
    quality_warnings: tuple[str, ...]
    review_input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    real_order_sent: Literal[False] = False

    @model_validator(mode="after")
    def validate_result(self):
        if self.subject == ReviewSubject.EXECUTED_TRADE:
            if not isinstance(self.outcome, CompletedTradeOutcome):
                raise ValueError("executed review requires a completed trade outcome")
            if not self.included_in_performance:
                raise ValueError("executed review must be included in performance")
            if self.net_pnl is None or self.net_return is None:
                raise ValueError("executed review requires reconciled performance")
        else:
            if not isinstance(self.outcome, NonExecutionOutcome):
                raise ValueError("non-execution review requires a non-execution outcome")
            if self.included_in_performance:
                raise ValueError("non-execution review cannot be included in performance")
            if self.net_pnl is not None or self.net_return is not None:
                raise ValueError("non-execution review cannot report portfolio performance")
        if self.outcome_id != self.outcome.outcome_id:
            raise ValueError("review outcome_id must match frozen outcome")
        if self.outcome_input_hash != self.outcome.input_hash:
            raise ValueError("review outcome hash must match frozen outcome")
        expected_benchmark_hash = (
            None if self.benchmark is None else self.benchmark.input_hash
        )
        if self.benchmark_input_hash != expected_benchmark_hash:
            raise ValueError("review benchmark hash must match frozen benchmark")
        if self.factor_input_hashes != tuple(
            factor.input_hash for factor in self.factor_observations
        ):
            raise ValueError("review factor hashes must match frozen factors")
        return self


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("review timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _factor_groups(
    decision: FrozenDecisionSnapshot,
    factors: tuple[OutcomeFactorObservation, ...],
) -> dict[str, tuple[str, ...]]:
    codes = [factor.factor_code for factor in factors]
    if len(codes) != len(set(codes)):
        raise ValueError("factor observations must be unique by factor_code")
    return {
        "confirmed": tuple(
            factor.factor_code
            for factor in factors
            if factor.assessment == FactorAssessment.SUPPORTIVE
            and factor.factor_code in decision.reason_codes
        ),
        "materialized": tuple(
            factor.factor_code
            for factor in factors
            if factor.assessment == FactorAssessment.ADVERSE
            and factor.factor_code in decision.counterargument_codes
        ),
        "unobserved": tuple(
            factor.factor_code
            for factor in factors
            if factor.assessment == FactorAssessment.UNOBSERVED_AT_DECISION
            or factor.factor_code
            not in {*decision.reason_codes, *decision.counterargument_codes}
        ),
        "neutral": tuple(
            factor.factor_code
            for factor in factors
            if factor.assessment == FactorAssessment.NEUTRAL
            and factor.factor_code
            in {*decision.reason_codes, *decision.counterargument_codes}
        ),
    }


def _canonical_factors(
    factors: tuple[OutcomeFactorObservation, ...],
) -> tuple[OutcomeFactorObservation, ...]:
    return tuple(
        sorted(
            factors,
            key=lambda item: (
                item.observed_at.astimezone(timezone.utc),
                item.factor_code,
            ),
        )
    )


def _review_payload(
    decision: FrozenDecisionSnapshot,
    outcome: CompletedTradeOutcome | NonExecutionOutcome,
    benchmark: ReviewBenchmark | None,
    factors: tuple[OutcomeFactorObservation, ...],
    reviewed_at: datetime,
) -> dict:
    return {
        "review_version": SIMULATION_REVIEW_VERSION,
        "reviewed_at": _utc(reviewed_at),
        "decision": decision.model_dump(mode="json"),
        "outcome": outcome.model_dump(mode="json"),
        "benchmark": None if benchmark is None else benchmark.model_dump(mode="json"),
        "factors": [factor.model_dump(mode="json") for factor in factors],
    }


def simulation_review_input_hash(
    decision: FrozenDecisionSnapshot,
    outcome: CompletedTradeOutcome | NonExecutionOutcome,
    benchmark: ReviewBenchmark | None,
    factors: tuple[OutcomeFactorObservation, ...],
    reviewed_at: datetime,
) -> str:
    """Hash all frozen inputs needed to reproduce a review."""

    return stable_payload_hash(
        _review_payload(decision, outcome, benchmark, factors, reviewed_at)
    )


def _validate_common_cutoff(
    *,
    decision: FrozenDecisionSnapshot,
    outcome_at: datetime,
    outcome_available_at: datetime,
    reviewed_at: datetime,
    benchmark: ReviewBenchmark | None,
    factors: tuple[OutcomeFactorObservation, ...],
) -> None:
    cutoff = _utc(reviewed_at)
    if _utc(outcome_at) < _utc(decision.decision_at):
        raise ValueError("review outcome cannot precede the decision")
    if _utc(outcome_available_at) > cutoff:
        raise ValueError("review outcome was not available by reviewed_at")
    if benchmark is not None and _utc(benchmark.available_at) > cutoff:
        raise ValueError("benchmark was not available by reviewed_at")
    if any(_utc(factor.available_at) > cutoff for factor in factors):
        raise ValueError("factor evidence was not available by reviewed_at")


def _benchmark_metrics(
    benchmark: ReviewBenchmark | None,
    *,
    expected_start: datetime,
    expected_end: datetime,
    compared_return: float | None,
) -> tuple[str | None, float | None, float | None, tuple[str, ...]]:
    if benchmark is None:
        return None, None, None, ("benchmark_missing",)
    if (
        _utc(benchmark.period_start_at) != _utc(expected_start)
        or _utc(benchmark.period_end_at) != _utc(expected_end)
    ):
        raise ValueError("benchmark period must exactly match the reviewed period")
    benchmark_return = benchmark.end_value / benchmark.start_value - 1
    excess = None if compared_return is None else compared_return - benchmark_return
    return benchmark.symbol, benchmark_return, excess, ()


def _trade_excursions(
    decision: FrozenDecisionSnapshot,
    outcome: CompletedTradeOutcome,
) -> tuple[float, float, float, float, float]:
    if decision.execution_mode is None:
        raise ValueError("executed trade review requires execution_mode")
    direction = -1 if decision.execution_mode == TradeMode.MARGIN_SHORT else 1
    entry_unit = outcome.entry_price * outcome.entry_fx_rate_to_account
    exit_unit = outcome.exit_price * outcome.exit_fx_rate_to_account
    favorable_values = [direction * (exit_unit - entry_unit)]
    adverse_values = [direction * (exit_unit - entry_unit)]
    ordered_returns = [0.0]
    for item in outcome.price_observations:
        high_unit = item.high * item.fx_rate_to_account
        low_unit = item.low * item.fx_rate_to_account
        favorable_values.append(
            (high_unit - entry_unit)
            if direction == 1
            else (entry_unit - low_unit)
        )
        adverse_values.append(
            (low_unit - entry_unit)
            if direction == 1
            else (entry_unit - high_unit)
        )
        conservative_order = (
            (high_unit, low_unit, item.close * item.fx_rate_to_account)
            if direction == 1
            else (low_unit, high_unit, item.close * item.fx_rate_to_account)
        )
        ordered_returns.extend(
            direction * (value - entry_unit) / entry_unit
            for value in conservative_order
        )
    ordered_returns.append(direction * (exit_unit - entry_unit) / entry_unit)
    favorable_unit = max(0.0, *favorable_values)
    adverse_unit = min(0.0, *adverse_values)
    wealth = [1 + value for value in ordered_returns]
    peak = wealth[0]
    maximum_drawdown = 0.0
    for value in wealth:
        peak = max(peak, value)
        if peak > 0:
            maximum_drawdown = min(maximum_drawdown, value / peak - 1)
    return (
        favorable_unit * outcome.quantity,
        favorable_unit / entry_unit,
        adverse_unit * outcome.quantity,
        adverse_unit / entry_unit,
        maximum_drawdown,
    )


def review_completed_trade(
    decision: FrozenDecisionSnapshot,
    outcome: CompletedTradeOutcome,
    *,
    reviewed_at: datetime,
    benchmark: ReviewBenchmark | None = None,
    factors: tuple[OutcomeFactorObservation, ...] = (),
    reconciliation_tolerance: float = 0.01,
) -> SimulationReviewResult:
    """Review a closed virtual trade using only evidence available by the cutoff."""

    factors = _canonical_factors(factors)
    _validate_common_cutoff(
        decision=decision,
        outcome_at=outcome.exit_at,
        outcome_available_at=outcome.available_at,
        reviewed_at=reviewed_at,
        benchmark=benchmark,
        factors=factors,
    )
    if decision.execution_mode is None:
        raise ValueError("executed trade review requires execution_mode")
    if _utc(outcome.entry_at) < _utc(decision.decision_at):
        raise ValueError("trade entry cannot precede the decision")
    if any(_utc(item.available_at) > _utc(reviewed_at) for item in outcome.price_observations):
        raise ValueError("price observation was not available by reviewed_at")
    direction = -1 if decision.execution_mode == TradeMode.MARGIN_SHORT else 1
    entry_unit = outcome.entry_price * outcome.entry_fx_rate_to_account
    exit_unit = outcome.exit_price * outcome.exit_fx_rate_to_account
    entry_notional = entry_unit * outcome.quantity
    gross_pnl = direction * outcome.quantity * (exit_unit - entry_unit)
    deducted_cost = outcome.deducted_costs.total()
    embedded_cost = outcome.embedded_costs.total()
    net_pnl = gross_pnl + outcome.position_cashflow_pnl - deducted_cost
    if outcome.reported_net_pnl is not None and not math.isclose(
        net_pnl,
        outcome.reported_net_pnl,
        abs_tol=reconciliation_tolerance,
    ):
        raise ValueError("reported net PnL does not reconcile with frozen outcome")
    net_return = net_pnl / entry_notional
    status_reasons: tuple[str, ...] = ()
    if outcome.price_path_complete:
        mfe, mfe_rate, mae, mae_rate, path_drawdown = _trade_excursions(
            decision,
            outcome,
        )
    else:
        mfe = mfe_rate = mae = mae_rate = path_drawdown = None
        status_reasons = ("holding_period_price_path_unverified",)
    benchmark_symbol, benchmark_return, excess_return, benchmark_warnings = (
        _benchmark_metrics(
            benchmark,
            expected_start=outcome.entry_at,
            expected_end=outcome.exit_at,
            compared_return=net_return,
        )
    )
    groups = _factor_groups(decision, factors)
    warnings = tuple(
        dict.fromkeys(
            (
                *decision.quality_warnings,
                *outcome.quality_warnings,
                *benchmark_warnings,
                *status_reasons,
                "research_only",
                "not_investment_advice",
            )
        )
    )
    review_input_hash = simulation_review_input_hash(
        decision,
        outcome,
        benchmark,
        factors,
        reviewed_at,
    )
    return SimulationReviewResult(
        review_id=stable_payload_hash(
            {"kind": "simulation_review", "input_hash": review_input_hash}
        ),
        reviewed_at=_utc(reviewed_at),
        subject=ReviewSubject.EXECUTED_TRADE,
        status=(
            ReviewStatus.COMPLETE
            if not status_reasons
            else ReviewStatus.INSUFFICIENT_DATA
        ),
        status_reasons=status_reasons,
        decision=decision,
        outcome=outcome,
        benchmark=benchmark,
        factor_observations=factors,
        outcome_id=outcome.outcome_id,
        outcome_at=_utc(outcome.exit_at),
        outcome_input_hash=outcome.input_hash,
        benchmark_input_hash=None if benchmark is None else benchmark.input_hash,
        factor_input_hashes=tuple(factor.input_hash for factor in factors),
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        net_return=net_return,
        total_deducted_cost=deducted_cost,
        total_embedded_cost=embedded_cost,
        position_cashflow_pnl=outcome.position_cashflow_pnl,
        maximum_favorable_excursion=mfe,
        maximum_favorable_excursion_rate=mfe_rate,
        maximum_adverse_excursion=mae,
        maximum_adverse_excursion_rate=mae_rate,
        position_path_maximum_drawdown=path_drawdown,
        account_maximum_drawdown=outcome.account_maximum_drawdown,
        holding_sessions=outcome.holding_sessions,
        benchmark_symbol=benchmark_symbol,
        benchmark_return=benchmark_return,
        excess_return=excess_return,
        observed_asset_return_if_no_execution=None,
        observed_asset_excess_return_if_no_execution=None,
        included_in_performance=True,
        confirmed_reason_codes=groups["confirmed"],
        materialized_counterargument_codes=groups["materialized"],
        unobserved_factor_codes=groups["unobserved"],
        neutral_factor_codes=groups["neutral"],
        quality_warnings=warnings,
        review_input_hash=review_input_hash,
    )


def review_non_execution(
    decision: FrozenDecisionSnapshot,
    outcome: NonExecutionOutcome,
    *,
    reviewed_at: datetime,
    benchmark: ReviewBenchmark | None = None,
    factors: tuple[OutcomeFactorObservation, ...] = (),
) -> SimulationReviewResult:
    """Review a skip/unfilled result without turning it into portfolio performance."""

    factors = _canonical_factors(factors)
    _validate_common_cutoff(
        decision=decision,
        outcome_at=outcome.outcome_at,
        outcome_available_at=outcome.available_at,
        reviewed_at=reviewed_at,
        benchmark=benchmark,
        factors=factors,
    )
    values = (
        outcome.reference_price,
        outcome.reference_fx_rate_to_account,
        outcome.observed_price,
        outcome.observed_fx_rate_to_account,
    )
    observed_return = None
    status_reasons: tuple[str, ...] = ()
    if all(value is not None for value in values):
        start = float(outcome.reference_price) * float(
            outcome.reference_fx_rate_to_account
        )
        end = float(outcome.observed_price) * float(
            outcome.observed_fx_rate_to_account
        )
        observed_return = end / start - 1
    else:
        status_reasons = ("counterfactual_price_observation_missing",)
    benchmark_symbol, benchmark_return, excess_return, benchmark_warnings = (
        _benchmark_metrics(
            benchmark,
            expected_start=decision.decision_at,
            expected_end=outcome.outcome_at,
            compared_return=observed_return,
        )
    )
    groups = _factor_groups(decision, factors)
    warnings = tuple(
        dict.fromkeys(
            (
                *decision.quality_warnings,
                *outcome.quality_warnings,
                *benchmark_warnings,
                "counterfactual_observation_excluded_from_performance",
                "research_only",
                "not_investment_advice",
            )
        )
    )
    review_input_hash = simulation_review_input_hash(
        decision,
        outcome,
        benchmark,
        factors,
        reviewed_at,
    )
    return SimulationReviewResult(
        review_id=stable_payload_hash(
            {"kind": "simulation_review", "input_hash": review_input_hash}
        ),
        reviewed_at=_utc(reviewed_at),
        subject=ReviewSubject(outcome.subject),
        status=(
            ReviewStatus.COMPLETE
            if not status_reasons
            else ReviewStatus.INSUFFICIENT_DATA
        ),
        status_reasons=status_reasons,
        decision=decision,
        outcome=outcome,
        benchmark=benchmark,
        factor_observations=factors,
        outcome_id=outcome.outcome_id,
        outcome_at=_utc(outcome.outcome_at),
        outcome_input_hash=outcome.input_hash,
        benchmark_input_hash=None if benchmark is None else benchmark.input_hash,
        factor_input_hashes=tuple(factor.input_hash for factor in factors),
        gross_pnl=None,
        net_pnl=None,
        net_return=None,
        total_deducted_cost=None,
        total_embedded_cost=None,
        position_cashflow_pnl=None,
        maximum_favorable_excursion=None,
        maximum_favorable_excursion_rate=None,
        maximum_adverse_excursion=None,
        maximum_adverse_excursion_rate=None,
        position_path_maximum_drawdown=None,
        account_maximum_drawdown=None,
        holding_sessions=None,
        benchmark_symbol=benchmark_symbol,
        benchmark_return=benchmark_return,
        excess_return=None,
        observed_asset_return_if_no_execution=observed_return,
        observed_asset_excess_return_if_no_execution=excess_return,
        included_in_performance=False,
        confirmed_reason_codes=groups["confirmed"],
        materialized_counterargument_codes=groups["materialized"],
        unobserved_factor_codes=groups["unobserved"],
        neutral_factor_codes=groups["neutral"],
        quality_warnings=warnings,
        review_input_hash=review_input_hash,
    )
