"""Deterministic MT-P2 margin analysis cards and conservative risk gates.

This module only evaluates a frozen point-in-time input. It never creates an
order, mutates an account, or infers missing market or broker data.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.analysis.trade_modes import (
    EligibilityStatus,
    TradeMode,
    assess_trade_mode_data,
)
from app.backtest.audit import stable_payload_hash
from app.providers.margin import (
    MarginAssetType,
    MarginDataQuality,
    MarginMarket,
    MarginTradingSnapshot,
    ShortAvailability,
)


MARGIN_ANALYSIS_CARD_VERSION = "margin-analysis-card-v1"
MARGIN_RISK_RULE_VERSION = "margin-risk-gates-v1"


class MarketRegime(StrEnum):
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"
    UNKNOWN = "unknown"


class EtfLeverageProfile(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    STANDARD = "standard"
    LEVERAGED = "leveraged"
    INVERSE = "inverse"


class MarginAnalysisStatus(StrEnum):
    CANDIDATE = "candidate"
    WARNING = "warning"
    BLOCKED = "blocked"
    NOT_ELIGIBLE = "not_eligible"
    INSUFFICIENT_DATA = "insufficient_data"


class MarginRiskLevel(StrEnum):
    MODERATE = "moderate"
    HIGH = "high"
    VERY_HIGH = "very_high"
    BLOCKED = "blocked"


class MarginFindingSeverity(StrEnum):
    WARNING = "warning"
    HARD_BLOCK = "hard_block"


class MarginRiskFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(pattern=r"^[a-z0-9_]+$")
    severity: MarginFindingSeverity
    message: str = Field(min_length=1)
    observed_value: float | int | str | None = None
    threshold: float | int | str | None = None


class MarginRiskPolicy(BaseModel):
    """Versioned research thresholds, not broker rules or investment advice."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = MARGIN_RISK_RULE_VERSION
    margin_data_maximum_age_days: int = Field(default=7, gt=0)
    minimum_traded_value_jpy: float = Field(default=50_000_000, gt=0)
    minimum_traded_value_usd: float = Field(default=1_000_000, gt=0)
    volume_ratio_warning: float = Field(default=1.0, gt=0)
    high_atr_pct: float = Field(default=0.06, gt=0)
    extreme_atr_pct: float = Field(default=0.12, gt=0)
    high_gap_risk_pct: float = Field(default=0.04, gt=0)
    extreme_gap_risk_pct: float = Field(default=0.08, gt=0)
    minimum_risk_reward_ratio: float = Field(default=1.5, gt=0)
    event_warning_days: int = Field(default=5, ge=0)
    event_block_days: int = Field(default=2, ge=0)
    maintenance_headroom_warning: float = Field(default=0.05, ge=0)
    maintenance_headroom_block: float = Field(default=0.0, ge=0)
    long_crowding_ratio_warning: float = Field(default=5.0, gt=0)
    long_crowding_ratio_block: float = Field(default=10.0, gt=0)
    short_squeeze_ratio_warning: float = Field(default=0.5, gt=0)
    short_squeeze_ratio_block: float = Field(default=0.2, gt=0)
    margin_balance_change_warning: float = Field(default=0.25, gt=0)
    margin_balance_change_block: float = Field(default=0.50, gt=0)
    financing_cost_warning: float = Field(default=0.10, ge=0)
    financing_cost_block: float = Field(default=0.25, ge=0)
    nearby_price_level_pct: float = Field(default=0.03, ge=0)

    @model_validator(mode="after")
    def validate_threshold_order(self):
        if self.extreme_atr_pct <= self.high_atr_pct:
            raise ValueError("extreme_atr_pct must exceed high_atr_pct")
        if self.extreme_gap_risk_pct <= self.high_gap_risk_pct:
            raise ValueError("extreme_gap_risk_pct must exceed high_gap_risk_pct")
        if self.event_block_days > self.event_warning_days:
            raise ValueError("event_block_days cannot exceed event_warning_days")
        if self.maintenance_headroom_block > self.maintenance_headroom_warning:
            raise ValueError(
                "maintenance_headroom_block cannot exceed the warning threshold"
            )
        if self.long_crowding_ratio_block <= self.long_crowding_ratio_warning:
            raise ValueError(
                "long_crowding_ratio_block must exceed the warning threshold"
            )
        if self.short_squeeze_ratio_block >= self.short_squeeze_ratio_warning:
            raise ValueError(
                "short_squeeze_ratio_block must be below the warning threshold"
            )
        if self.margin_balance_change_block <= self.margin_balance_change_warning:
            raise ValueError(
                "margin_balance_change_block must exceed the warning threshold"
            )
        if self.financing_cost_block <= self.financing_cost_warning:
            raise ValueError("financing_cost_block must exceed the warning threshold")
        return self


class MarginAnalysisInput(BaseModel):
    """Point-in-time values calculated and validated outside the UI or an LLM."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str = Field(min_length=1, max_length=64)
    symbol: str = Field(pattern=r"^[A-Z0-9.\-]{1,32}$")
    market: MarginMarket
    asset_type: MarginAssetType
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    mode: TradeMode
    as_of: datetime
    data_as_of: datetime
    market_regime: MarketRegime = MarketRegime.UNKNOWN
    trend_return_20d: float | None = None
    volume_ratio_20d: float | None = Field(default=None, ge=0)
    average_traded_value_20d: float | None = Field(default=None, ge=0)
    atr_pct: float | None = Field(default=None, ge=0)
    gap_risk_pct: float | None = Field(default=None, ge=0)
    support_distance_pct: float | None = Field(default=None, ge=0)
    resistance_distance_pct: float | None = Field(default=None, ge=0)
    margin_long_balance_change_ratio_20d: float | None = None
    margin_short_balance_change_ratio_20d: float | None = None
    risk_reward_ratio: float | None = Field(default=None, ge=0)
    days_to_event: int | None = Field(default=None, ge=0)
    expected_holding_days: int = Field(gt=0)
    maintenance_headroom_pct: float | None = None
    etf_leverage_profile: EtfLeverageProfile = EtfLeverageProfile.NOT_APPLICABLE
    internal_leverage_multiple: float | None = Field(default=None, ge=1)
    upstream_quality_warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_point_in_time_boundary(self):
        for timestamp in (self.as_of, self.data_as_of):
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("analysis timestamps must be timezone-aware")
        if self.data_as_of > self.as_of:
            raise ValueError("data_as_of cannot be after as_of")
        if self.mode not in {TradeMode.MARGIN_LONG, TradeMode.MARGIN_SHORT}:
            raise ValueError("MT-P2 analysis accepts only margin_long or margin_short")
        if self.market == MarginMarket.JP and self.currency != "JPY":
            raise ValueError("Japanese analysis inputs must use JPY")
        if self.market == MarginMarket.US and self.currency != "USD":
            raise ValueError("US analysis inputs must use USD")
        if self.asset_type == MarginAssetType.STOCK:
            if self.etf_leverage_profile != EtfLeverageProfile.NOT_APPLICABLE:
                raise ValueError("stock inputs cannot use an ETF leverage profile")
            if self.internal_leverage_multiple is not None:
                raise ValueError("stock inputs cannot use internal ETF leverage")
        else:
            if self.etf_leverage_profile == EtfLeverageProfile.NOT_APPLICABLE:
                raise ValueError("ETF inputs must identify their leverage profile")
            if (
                self.etf_leverage_profile == EtfLeverageProfile.STANDARD
                and self.internal_leverage_multiple is not None
            ):
                raise ValueError("standard ETFs cannot declare internal leverage")
        return self


class MarginAnalysisCard(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    card_version: str = MARGIN_ANALYSIS_CARD_VERSION
    risk_rule_version: str
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    asset_id: str
    symbol: str
    market: MarginMarket
    asset_type: MarginAssetType
    mode: TradeMode
    as_of: datetime
    data_as_of: datetime
    provider_record_id: str | None = None
    eligibility_status: EligibilityStatus
    analysis_status: MarginAnalysisStatus
    decision: str
    risk_level: MarginRiskLevel
    supporting_factors: tuple[str, ...]
    risk_findings: tuple[MarginRiskFinding, ...]
    warning_codes: tuple[str, ...]
    hard_block_codes: tuple[str, ...]
    human_review_required: bool
    virtual_order_allowed: bool = False
    order_generation_status: str = "not_connected_mt_p2"


_HARD_RESTRICTIONS = {
    "trading_halt",
    "new_margin_positions_suspended",
    "margin_trading_suspended",
}
_LONG_HARD_RESTRICTIONS = {"margin_long_suspended"}
_SHORT_HARD_RESTRICTIONS = {"margin_short_suspended", "short_sale_prohibited"}
_WARNING_RESTRICTIONS = {
    "increased_margin_requirement",
    "short_sale_price_restriction",
}


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _minimum_traded_value(
    inputs: MarginAnalysisInput,
    policy: MarginRiskPolicy,
) -> float:
    return (
        policy.minimum_traded_value_jpy
        if inputs.currency == "JPY"
        else policy.minimum_traded_value_usd
    )


def _identity_matches(
    inputs: MarginAnalysisInput,
    snapshot: MarginTradingSnapshot,
) -> None:
    if inputs.asset_id != snapshot.asset_id:
        raise ValueError("analysis asset_id does not match the margin snapshot")
    if inputs.symbol.upper() != snapshot.symbol.upper():
        raise ValueError("analysis symbol does not match the margin snapshot")
    if inputs.market != snapshot.market:
        raise ValueError("analysis market does not match the margin snapshot")
    if inputs.asset_type != snapshot.asset_type:
        raise ValueError("analysis asset_type does not match the margin snapshot")
    if inputs.currency != snapshot.currency:
        raise ValueError("analysis currency does not match the margin snapshot")


def _finding(
    code: str,
    severity: MarginFindingSeverity,
    message: str,
    *,
    observed_value: float | int | str | None = None,
    threshold: float | int | str | None = None,
) -> MarginRiskFinding:
    return MarginRiskFinding(
        code=code,
        severity=severity,
        message=message,
        observed_value=observed_value,
        threshold=threshold,
    )


def _append_unique(
    findings: list[MarginRiskFinding],
    finding: MarginRiskFinding,
) -> None:
    if finding.code not in {item.code for item in findings}:
        findings.append(finding)


def _evaluate_required_market_inputs(
    inputs: MarginAnalysisInput,
    policy: MarginRiskPolicy,
    findings: list[MarginRiskFinding],
    supporting: list[str],
) -> None:
    required = {
        "trend_return_20d": inputs.trend_return_20d,
        "volume_ratio_20d": inputs.volume_ratio_20d,
        "average_traded_value_20d": inputs.average_traded_value_20d,
        "atr_pct": inputs.atr_pct,
        "gap_risk_pct": inputs.gap_risk_pct,
        "risk_reward_ratio": inputs.risk_reward_ratio,
        "days_to_event": inputs.days_to_event,
        "maintenance_headroom_pct": inputs.maintenance_headroom_pct,
    }
    for field_name, value in required.items():
        if value is None:
            _append_unique(
                findings,
                _finding(
                    f"missing_{field_name}",
                    MarginFindingSeverity.HARD_BLOCK,
                    f"Required point-in-time input {field_name} is missing.",
                ),
            )

    minimum_value = _minimum_traded_value(inputs, policy)
    if inputs.average_traded_value_20d is not None:
        if inputs.average_traded_value_20d < minimum_value:
            _append_unique(
                findings,
                _finding(
                    "liquidity_below_minimum",
                    MarginFindingSeverity.HARD_BLOCK,
                    "Average traded value is below the versioned liquidity floor.",
                    observed_value=inputs.average_traded_value_20d,
                    threshold=minimum_value,
                ),
            )
        else:
            supporting.append("liquidity_floor_passed")
    if inputs.volume_ratio_20d is not None:
        if inputs.volume_ratio_20d < policy.volume_ratio_warning:
            _append_unique(
                findings,
                _finding(
                    "volume_not_expanding",
                    MarginFindingSeverity.WARNING,
                    "Recent volume is below the versioned expansion threshold.",
                    observed_value=inputs.volume_ratio_20d,
                    threshold=policy.volume_ratio_warning,
                ),
            )
        else:
            supporting.append("volume_confirmation")
    if inputs.risk_reward_ratio is not None:
        if inputs.risk_reward_ratio < policy.minimum_risk_reward_ratio:
            _append_unique(
                findings,
                _finding(
                    "risk_reward_below_minimum",
                    MarginFindingSeverity.HARD_BLOCK,
                    "Planned risk/reward is below the versioned minimum.",
                    observed_value=inputs.risk_reward_ratio,
                    threshold=policy.minimum_risk_reward_ratio,
                ),
            )
        else:
            supporting.append("risk_reward_floor_passed")

    if inputs.days_to_event is not None:
        if inputs.days_to_event <= policy.event_block_days:
            _append_unique(
                findings,
                _finding(
                    "event_imminent",
                    MarginFindingSeverity.HARD_BLOCK,
                    "A known earnings or material event is too close for a new margin position.",
                    observed_value=inputs.days_to_event,
                    threshold=policy.event_block_days,
                ),
            )
        elif inputs.days_to_event <= policy.event_warning_days:
            _append_unique(
                findings,
                _finding(
                    "event_near",
                    MarginFindingSeverity.WARNING,
                    "A known earnings or material event is approaching.",
                    observed_value=inputs.days_to_event,
                    threshold=policy.event_warning_days,
                ),
            )
        else:
            supporting.append("event_distance_passed")

    if inputs.atr_pct is not None:
        if inputs.atr_pct >= policy.extreme_atr_pct:
            _append_unique(
                findings,
                _finding(
                    "extreme_volatility",
                    MarginFindingSeverity.HARD_BLOCK,
                    "ATR is above the extreme-volatility limit.",
                    observed_value=inputs.atr_pct,
                    threshold=policy.extreme_atr_pct,
                ),
            )
        elif inputs.atr_pct >= policy.high_atr_pct:
            _append_unique(
                findings,
                _finding(
                    "high_volatility",
                    MarginFindingSeverity.WARNING,
                    "ATR is above the high-volatility warning threshold.",
                    observed_value=inputs.atr_pct,
                    threshold=policy.high_atr_pct,
                ),
            )
        else:
            supporting.append("atr_within_policy")

    if inputs.gap_risk_pct is not None:
        if inputs.gap_risk_pct >= policy.extreme_gap_risk_pct:
            _append_unique(
                findings,
                _finding(
                    "extreme_gap_risk",
                    MarginFindingSeverity.HARD_BLOCK,
                    "Observed gap risk is above the hard limit.",
                    observed_value=inputs.gap_risk_pct,
                    threshold=policy.extreme_gap_risk_pct,
                ),
            )
        elif inputs.gap_risk_pct >= policy.high_gap_risk_pct:
            _append_unique(
                findings,
                _finding(
                    "high_gap_risk",
                    MarginFindingSeverity.WARNING,
                    "Observed gap risk is above the warning threshold.",
                    observed_value=inputs.gap_risk_pct,
                    threshold=policy.high_gap_risk_pct,
                ),
            )
        else:
            supporting.append("gap_risk_within_policy")

    if inputs.maintenance_headroom_pct is not None:
        if inputs.maintenance_headroom_pct <= policy.maintenance_headroom_block:
            _append_unique(
                findings,
                _finding(
                    "maintenance_headroom_exhausted",
                    MarginFindingSeverity.HARD_BLOCK,
                    "Maintenance-margin headroom is exhausted.",
                    observed_value=inputs.maintenance_headroom_pct,
                    threshold=policy.maintenance_headroom_block,
                ),
            )
        elif inputs.maintenance_headroom_pct <= policy.maintenance_headroom_warning:
            _append_unique(
                findings,
                _finding(
                    "maintenance_headroom_low",
                    MarginFindingSeverity.WARNING,
                    "Maintenance-margin headroom is near the warning threshold.",
                    observed_value=inputs.maintenance_headroom_pct,
                    threshold=policy.maintenance_headroom_warning,
                ),
            )
        else:
            supporting.append("maintenance_headroom_passed")


def _evaluate_directional_inputs(
    inputs: MarginAnalysisInput,
    snapshot: MarginTradingSnapshot,
    policy: MarginRiskPolicy,
    findings: list[MarginRiskFinding],
    supporting: list[str],
) -> None:
    is_long = inputs.mode == TradeMode.MARGIN_LONG
    if inputs.trend_return_20d is not None:
        trend_passed = inputs.trend_return_20d > 0 if is_long else inputs.trend_return_20d < 0
        if trend_passed:
            supporting.append("uptrend_confirmed" if is_long else "downtrend_confirmed")
        else:
            _append_unique(
                findings,
                _finding(
                    "long_trend_not_confirmed" if is_long else "short_trend_not_confirmed",
                    MarginFindingSeverity.HARD_BLOCK,
                    "The required directional trend is not confirmed.",
                    observed_value=inputs.trend_return_20d,
                    threshold=0,
                ),
            )

    aligned_regime = (
        MarketRegime.BULLISH if is_long else MarketRegime.BEARISH
    )
    opposing_regime = (
        MarketRegime.BEARISH if is_long else MarketRegime.BULLISH
    )
    if inputs.market_regime == aligned_regime:
        supporting.append("market_regime_aligned")
    elif inputs.market_regime == opposing_regime:
        _append_unique(
            findings,
            _finding(
                "market_regime_opposes_direction",
                MarginFindingSeverity.WARNING,
                "The market regime opposes the proposed margin direction.",
                observed_value=inputs.market_regime.value,
                threshold=aligned_regime.value,
            ),
        )
    else:
        _append_unique(
            findings,
            _finding(
                "market_regime_not_confirmed",
                MarginFindingSeverity.WARNING,
                "The market regime does not confirm the proposed direction.",
                observed_value=inputs.market_regime.value,
                threshold=aligned_regime.value,
            ),
        )

    price_level_distance = (
        inputs.support_distance_pct if is_long else inputs.resistance_distance_pct
    )
    level_name = "support" if is_long else "resistance"
    if price_level_distance is None:
        _append_unique(
            findings,
            _finding(
                f"{level_name}_level_missing",
                MarginFindingSeverity.WARNING,
                f"A confirmed {level_name} level is unavailable.",
            ),
        )
    elif price_level_distance <= policy.nearby_price_level_pct:
        supporting.append(f"nearby_{level_name}_confirmed")
    else:
        _append_unique(
            findings,
            _finding(
                f"{level_name}_not_nearby",
                MarginFindingSeverity.WARNING,
                f"The confirmed {level_name} level is not nearby.",
                observed_value=price_level_distance,
                threshold=policy.nearby_price_level_pct,
            ),
        )

    if snapshot.lending_ratio is None:
        _append_unique(
            findings,
            _finding(
                "lending_ratio_missing",
                MarginFindingSeverity.WARNING,
                "The point-in-time lending ratio is unavailable.",
            ),
        )
    elif is_long:
        if snapshot.lending_ratio >= policy.long_crowding_ratio_block:
            _append_unique(
                findings,
                _finding(
                    "margin_long_crowding_extreme",
                    MarginFindingSeverity.HARD_BLOCK,
                    "The lending ratio indicates extreme margin-long crowding.",
                    observed_value=snapshot.lending_ratio,
                    threshold=policy.long_crowding_ratio_block,
                ),
            )
        elif snapshot.lending_ratio >= policy.long_crowding_ratio_warning:
            _append_unique(
                findings,
                _finding(
                    "margin_long_crowding_high",
                    MarginFindingSeverity.WARNING,
                    "The lending ratio indicates elevated margin-long crowding.",
                    observed_value=snapshot.lending_ratio,
                    threshold=policy.long_crowding_ratio_warning,
                ),
            )
        else:
            supporting.append("margin_long_crowding_within_policy")
    else:
        if snapshot.lending_ratio <= policy.short_squeeze_ratio_block:
            _append_unique(
                findings,
                _finding(
                    "short_squeeze_risk_extreme",
                    MarginFindingSeverity.HARD_BLOCK,
                    "The lending ratio indicates extreme short crowding and squeeze risk.",
                    observed_value=snapshot.lending_ratio,
                    threshold=policy.short_squeeze_ratio_block,
                ),
            )
        elif snapshot.lending_ratio <= policy.short_squeeze_ratio_warning:
            _append_unique(
                findings,
                _finding(
                    "short_squeeze_risk_high",
                    MarginFindingSeverity.WARNING,
                    "The lending ratio indicates elevated short crowding and squeeze risk.",
                    observed_value=snapshot.lending_ratio,
                    threshold=policy.short_squeeze_ratio_warning,
                ),
            )
        else:
            supporting.append("short_squeeze_risk_within_policy")

    balance = (
        snapshot.margin_long_balance if is_long else snapshot.margin_short_balance
    )
    if balance is None:
        _append_unique(
            findings,
            _finding(
                "margin_balance_missing",
                MarginFindingSeverity.WARNING,
                "The directional margin balance is unavailable.",
            ),
        )
    balance_change = (
        inputs.margin_long_balance_change_ratio_20d
        if is_long
        else inputs.margin_short_balance_change_ratio_20d
    )
    if balance_change is None:
        _append_unique(
            findings,
            _finding(
                "margin_balance_change_missing",
                MarginFindingSeverity.WARNING,
                "The point-in-time directional margin-balance change is unavailable.",
            ),
        )
    elif balance_change >= policy.margin_balance_change_block:
        _append_unique(
            findings,
            _finding(
                (
                    "margin_long_balance_growth_extreme"
                    if is_long
                    else "margin_short_balance_growth_extreme"
                ),
                MarginFindingSeverity.HARD_BLOCK,
                "Directional margin balances increased beyond the hard limit.",
                observed_value=balance_change,
                threshold=policy.margin_balance_change_block,
            ),
        )
    elif balance_change >= policy.margin_balance_change_warning:
        _append_unique(
            findings,
            _finding(
                (
                    "margin_long_balance_growth_high"
                    if is_long
                    else "margin_short_balance_growth_high"
                ),
                MarginFindingSeverity.WARNING,
                "Directional margin balances increased beyond the warning threshold.",
                observed_value=balance_change,
                threshold=policy.margin_balance_change_warning,
            ),
        )
    else:
        supporting.append("margin_balance_growth_within_policy")


def _evaluate_financing_and_term(
    inputs: MarginAnalysisInput,
    snapshot: MarginTradingSnapshot,
    policy: MarginRiskPolicy,
    findings: list[MarginRiskFinding],
    supporting: list[str],
) -> None:
    if inputs.expected_holding_days >= (snapshot.repayment_term_days or 0):
        _append_unique(
            findings,
            _finding(
                "repayment_term_too_short",
                MarginFindingSeverity.HARD_BLOCK,
                "The planned holding period reaches or exceeds the repayment term.",
                observed_value=inputs.expected_holding_days,
                threshold=snapshot.repayment_term_days,
            ),
        )
    elif inputs.expected_holding_days * 2 >= (snapshot.repayment_term_days or 0):
        _append_unique(
            findings,
            _finding(
                "repayment_term_near",
                MarginFindingSeverity.WARNING,
                "The planned holding period uses at least half of the repayment term.",
                observed_value=inputs.expected_holding_days,
                threshold=snapshot.repayment_term_days,
            ),
        )
    else:
        supporting.append("repayment_term_headroom_passed")

    if inputs.mode == TradeMode.MARGIN_LONG:
        annualized_cost = snapshot.margin_interest_rate
    else:
        costs = [snapshot.stock_lending_fee, snapshot.borrow_cost]
        annualized_cost = sum(value for value in costs if value is not None)
    if annualized_cost is not None:
        if annualized_cost >= policy.financing_cost_block:
            _append_unique(
                findings,
                _finding(
                    "financing_cost_extreme",
                    MarginFindingSeverity.HARD_BLOCK,
                    "Known annualized financing cost exceeds the hard limit.",
                    observed_value=annualized_cost,
                    threshold=policy.financing_cost_block,
                ),
            )
        elif annualized_cost >= policy.financing_cost_warning:
            _append_unique(
                findings,
                _finding(
                    "financing_cost_high",
                    MarginFindingSeverity.WARNING,
                    "Known annualized financing cost exceeds the warning threshold.",
                    observed_value=annualized_cost,
                    threshold=policy.financing_cost_warning,
                ),
            )
        else:
            supporting.append("financing_cost_within_policy")
    if (
        inputs.mode == TradeMode.MARGIN_SHORT
        and snapshot.reverse_stock_borrow_fee is not None
        and snapshot.reverse_stock_borrow_fee > 0
    ):
        _append_unique(
            findings,
            _finding(
                "reverse_stock_borrow_fee_present",
                MarginFindingSeverity.WARNING,
                "A point-in-time reverse stock-borrow fee is present.",
                observed_value=snapshot.reverse_stock_borrow_fee,
            ),
        )


def _evaluate_restrictions(
    inputs: MarginAnalysisInput,
    snapshot: MarginTradingSnapshot,
    findings: list[MarginRiskFinding],
) -> None:
    mode_hard = (
        _LONG_HARD_RESTRICTIONS
        if inputs.mode == TradeMode.MARGIN_LONG
        else _SHORT_HARD_RESTRICTIONS
    )
    recognized = _HARD_RESTRICTIONS | mode_hard | _WARNING_RESTRICTIONS
    for restriction in snapshot.restriction_codes:
        if restriction in _HARD_RESTRICTIONS or restriction in mode_hard:
            severity = MarginFindingSeverity.HARD_BLOCK
            code = f"restriction_{restriction}"
            message = "A normalized provider restriction prohibits this margin candidate."
        elif restriction in _WARNING_RESTRICTIONS:
            severity = MarginFindingSeverity.WARNING
            code = f"restriction_{restriction}"
            message = "A normalized provider restriction requires human review."
        elif restriction not in recognized:
            severity = MarginFindingSeverity.WARNING
            code = "unmapped_provider_restriction"
            message = "An unmapped provider restriction must not be interpreted automatically."
        else:
            continue
        _append_unique(
            findings,
            _finding(
                code,
                severity,
                message,
                observed_value=restriction,
            ),
        )


def _evaluate_leveraged_etf(
    inputs: MarginAnalysisInput,
    findings: list[MarginRiskFinding],
) -> bool:
    leveraged = inputs.etf_leverage_profile in {
        EtfLeverageProfile.LEVERAGED,
        EtfLeverageProfile.INVERSE,
    }
    if not leveraged:
        return False
    warnings = (
        ("etf_credit_leverage_overlap", "ETF internal leverage overlaps margin leverage."),
        ("leveraged_etf_volatility_risk", "Leveraged or inverse ETF volatility is amplified."),
        ("leveraged_etf_path_dependency", "Longer holding periods can diverge from the index."),
        ("leveraged_etf_gap_risk", "Gap risk can reduce maintenance-margin headroom."),
        ("leveraged_etf_forced_liquidation_risk", "Forced-liquidation risk is elevated."),
    )
    for code, message in warnings:
        _append_unique(
            findings,
            _finding(code, MarginFindingSeverity.WARNING, message),
        )
    if inputs.internal_leverage_multiple is None:
        _append_unique(
            findings,
            _finding(
                "etf_internal_leverage_unknown",
                MarginFindingSeverity.WARNING,
                "The internal ETF leverage multiple is unknown and was not guessed.",
            ),
        )
    return True


def _risk_level(
    findings: list[MarginRiskFinding],
    *,
    leveraged_etf: bool,
) -> MarginRiskLevel:
    if any(item.severity == MarginFindingSeverity.HARD_BLOCK for item in findings):
        return MarginRiskLevel.BLOCKED
    leverage_codes = {
        "etf_credit_leverage_overlap",
        "leveraged_etf_volatility_risk",
        "leveraged_etf_path_dependency",
        "leveraged_etf_gap_risk",
        "leveraged_etf_forced_liquidation_risk",
        "etf_internal_leverage_unknown",
    }
    non_leverage_warning = any(item.code not in leverage_codes for item in findings)
    level = 1 + int(non_leverage_warning) + int(leveraged_etf)
    return {
        1: MarginRiskLevel.MODERATE,
        2: MarginRiskLevel.HIGH,
        3: MarginRiskLevel.VERY_HIGH,
    }[min(level, 3)]


def build_margin_analysis_card(
    inputs: MarginAnalysisInput,
    snapshot: MarginTradingSnapshot | None,
    *,
    policy: MarginRiskPolicy | None = None,
) -> MarginAnalysisCard:
    """Build an auditable analysis card without generating a virtual order."""

    selected_policy = policy or MarginRiskPolicy()
    if snapshot is not None:
        _identity_matches(inputs, snapshot)
    eligibility = assess_trade_mode_data(
        inputs.mode,
        snapshot,
        as_of=inputs.as_of,
        maximum_age=timedelta(days=selected_policy.margin_data_maximum_age_days),
    )
    findings: list[MarginRiskFinding] = []
    supporting: list[str] = []
    for reason in eligibility.reason_codes:
        _append_unique(
            findings,
            _finding(
                reason,
                MarginFindingSeverity.HARD_BLOCK,
                "The margin-data eligibility boundary did not pass.",
            ),
        )

    leveraged_etf = False
    if eligibility.status == EligibilityStatus.ELIGIBLE and snapshot is not None:
        _evaluate_required_market_inputs(inputs, selected_policy, findings, supporting)
        _evaluate_directional_inputs(
            inputs,
            snapshot,
            selected_policy,
            findings,
            supporting,
        )
        _evaluate_financing_and_term(
            inputs,
            snapshot,
            selected_policy,
            findings,
            supporting,
        )
        _evaluate_restrictions(inputs, snapshot, findings)
        leveraged_etf = _evaluate_leveraged_etf(inputs, findings)
        if snapshot.data_quality == MarginDataQuality.PARTIAL:
            _append_unique(
                findings,
                _finding(
                    "margin_data_partial",
                    MarginFindingSeverity.WARNING,
                    "The provider marked the margin snapshot as partial.",
                ),
            )
        if (
            inputs.mode == TradeMode.MARGIN_SHORT
            and snapshot.short_availability == ShortAvailability.LIMITED
        ):
            _append_unique(
                findings,
                _finding(
                    "short_inventory_limited",
                    MarginFindingSeverity.WARNING,
                    "Short inventory is limited and requires rechecking before use.",
                ),
            )
        for warning in inputs.upstream_quality_warnings:
            _append_unique(
                findings,
                _finding(
                    "upstream_quality_warning",
                    MarginFindingSeverity.WARNING,
                    "An upstream deterministic analysis reported a quality warning.",
                    observed_value=warning,
                ),
            )

    hard_codes = tuple(
        item.code
        for item in findings
        if item.severity == MarginFindingSeverity.HARD_BLOCK
    )
    warning_codes = tuple(
        item.code
        for item in findings
        if item.severity == MarginFindingSeverity.WARNING
    )
    if eligibility.status == EligibilityStatus.NOT_ELIGIBLE:
        status = MarginAnalysisStatus.NOT_ELIGIBLE
    elif eligibility.status == EligibilityStatus.INSUFFICIENT_DATA:
        status = MarginAnalysisStatus.INSUFFICIENT_DATA
    elif hard_codes:
        status = MarginAnalysisStatus.BLOCKED
    elif warning_codes:
        status = MarginAnalysisStatus.WARNING
    else:
        status = MarginAnalysisStatus.CANDIDATE
    decision = {
        MarginAnalysisStatus.CANDIDATE: (
            "信用買い候補"
            if inputs.mode == TradeMode.MARGIN_LONG
            else "信用売り候補"
        ),
        MarginAnalysisStatus.WARNING: (
            "条件付き信用買い候補"
            if inputs.mode == TradeMode.MARGIN_LONG
            else "条件付き信用売り候補"
        ),
        MarginAnalysisStatus.BLOCKED: "信用取引不可",
        MarginAnalysisStatus.NOT_ELIGIBLE: "信用取引不可",
        MarginAnalysisStatus.INSUFFICIENT_DATA: "データ不足",
    }[status]
    payload = {
        "inputs": inputs.model_dump(mode="json"),
        "snapshot": None if snapshot is None else snapshot.model_dump(mode="json"),
        "policy": selected_policy.model_dump(mode="json"),
    }
    return MarginAnalysisCard(
        risk_rule_version=selected_policy.version,
        input_hash=stable_payload_hash(payload),
        asset_id=inputs.asset_id,
        symbol=inputs.symbol,
        market=inputs.market,
        asset_type=inputs.asset_type,
        mode=inputs.mode,
        as_of=_utc(inputs.as_of),
        data_as_of=_utc(inputs.data_as_of),
        provider_record_id=(None if snapshot is None else snapshot.provider_record_id),
        eligibility_status=eligibility.status,
        analysis_status=status,
        decision=decision,
        risk_level=_risk_level(findings, leveraged_etf=leveraged_etf),
        supporting_factors=tuple(dict.fromkeys(supporting)),
        risk_findings=tuple(findings),
        warning_codes=warning_codes,
        hard_block_codes=hard_codes,
        human_review_required=(
            eligibility.human_review_required or bool(warning_codes) or bool(hard_codes)
        ),
    )
