"""Pure MT-P3 margin-position state transitions for the OHLC engine.

The module models virtual margin collateral, financing costs, maintenance
requirements and conservative liquidation. It has no broker connectivity and
does not place or schedule real orders.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import StrEnum
import math

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.analysis.margin_risk import MarginAnalysisCard, MarginAnalysisStatus
from app.analysis.trade_modes import TradeMode
from app.backtest.audit import stable_payload_hash
from app.providers.margin import MarginMarket, MarginTradingSnapshot


MARGIN_POSITION_ENGINE_VERSION = "margin-position-engine-v1"
MARGIN_EXECUTION_POLICY_VERSION = "margin-execution-policy-v1"


class MarginTermsBasis(StrEnum):
    VERIFIED_PROVIDER = "verified_provider"
    VERSIONED_RESEARCH_PROXY = "versioned_research_proxy"


class MarginEventType(StrEnum):
    ENTRY = "entry"
    EXIT = "exit"
    REJECTED = "rejected"
    FINANCING_ACCRUAL = "financing_accrual"
    MAINTENANCE_WARNING = "maintenance_warning"
    FORCED_LIQUIDATION_SCHEDULED = "forced_liquidation_scheduled"
    FORCED_LIQUIDATION_DEFERRED = "forced_liquidation_deferred"
    EXIT_DEFERRED = "exit_deferred"


class MarginExecutionPolicy(BaseModel):
    """Versioned conservative assumptions for virtual margin positions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = MARGIN_EXECUTION_POLICY_VERSION
    fee_rate: float = Field(default=0.001, ge=0, le=0.05)
    spread_rate: float = Field(default=0.001, ge=0, le=0.05)
    slippage_rate: float = Field(default=0.0005, ge=0, le=0.05)
    annual_day_count: int = Field(default=365, gt=0)
    lot_size: int = Field(default=100, gt=0)
    maximum_positions: int = Field(default=4, gt=0)
    maximum_volume_participation: float = Field(default=0.10, gt=0, le=1)
    maximum_risk_per_trade_rate: float = Field(default=0.01, gt=0, lt=1)
    maximum_total_open_risk_rate: float = Field(default=0.05, gt=0, lt=1)
    maximum_position_notional_rate: float = Field(default=0.50, gt=0)
    maximum_sector_notional_rate: float = Field(default=0.75, gt=0)
    maximum_correlation_notional_rate: float = Field(default=0.75, gt=0)
    maximum_gross_leverage: float = Field(default=2.0, gt=0)
    maintenance_warning_buffer: float = Field(default=0.05, ge=0)
    simultaneous_hit_policy: str = "stop_first"
    forced_liquidation_timing: str = "next_session_open"

    @model_validator(mode="after")
    def validate_policy(self):
        if self.maximum_risk_per_trade_rate > self.maximum_total_open_risk_rate:
            raise ValueError("per-trade risk cannot exceed total open risk")
        if self.maximum_position_notional_rate > self.maximum_gross_leverage:
            raise ValueError("position notional limit cannot exceed gross leverage")
        if self.maximum_sector_notional_rate > self.maximum_gross_leverage:
            raise ValueError("sector notional limit cannot exceed gross leverage")
        if self.maximum_correlation_notional_rate > self.maximum_gross_leverage:
            raise ValueError("correlation limit cannot exceed gross leverage")
        if self.simultaneous_hit_policy != "stop_first":
            raise ValueError("only conservative stop_first is supported")
        if self.forced_liquidation_timing != "next_session_open":
            raise ValueError("only next_session_open forced liquidation is supported")
        return self


class MarginPositionTerms(BaseModel):
    """Frozen provider or explicitly versioned research terms at decision time."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_record_id: str = Field(min_length=1, max_length=128)
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    asset_id: str = Field(min_length=1, max_length=64)
    symbol: str = Field(pattern=r"^[A-Z0-9.\-]{1,32}$")
    source: str = Field(min_length=1, max_length=64)
    source_version: str = Field(min_length=1, max_length=64)
    terms_basis: MarginTermsBasis
    market: MarginMarket
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    mode: TradeMode
    initial_margin_rate: float = Field(gt=0, le=1)
    maintenance_margin_rate: float = Field(gt=0, le=1)
    minimum_margin_amount: float = Field(ge=0)
    margin_interest_rate: float | None = Field(default=None, ge=0)
    stock_lending_fee: float | None = Field(default=None, ge=0)
    borrow_cost: float | None = Field(default=None, ge=0)
    reverse_stock_borrow_fee_per_share_day: float | None = Field(
        default=None,
        ge=0,
    )
    repayment_term_days: int = Field(gt=0)
    forced_liquidation_rule_version: str = Field(min_length=1, max_length=64)
    effective_from: datetime
    effective_to: datetime | None = None
    available_at: datetime
    fetched_at: datetime

    @model_validator(mode="after")
    def validate_terms(self):
        timestamps = [self.effective_from, self.available_at, self.fetched_at]
        if self.effective_to is not None:
            timestamps.append(self.effective_to)
        if any(value.tzinfo is None or value.utcoffset() is None for value in timestamps):
            raise ValueError("margin term timestamps must be timezone-aware")
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be after effective_from")
        if self.fetched_at < self.available_at:
            raise ValueError("fetched_at cannot be before available_at")
        if self.initial_margin_rate < self.maintenance_margin_rate:
            raise ValueError("initial margin rate cannot be below maintenance rate")
        if self.mode == TradeMode.MARGIN_LONG:
            if self.margin_interest_rate is None:
                raise ValueError("margin_long terms require margin_interest_rate")
            if any(
                value is not None
                for value in (
                    self.stock_lending_fee,
                    self.borrow_cost,
                    self.reverse_stock_borrow_fee_per_share_day,
                )
            ):
                raise ValueError("margin_long terms cannot contain short-only costs")
        elif self.mode == TradeMode.MARGIN_SHORT:
            if self.stock_lending_fee is None:
                raise ValueError("margin_short terms require stock_lending_fee")
            if self.market == MarginMarket.US and self.borrow_cost is None:
                raise ValueError("US margin_short terms require borrow_cost")
            if (
                self.market == MarginMarket.US
                and self.reverse_stock_borrow_fee_per_share_day is not None
            ):
                raise ValueError("US margin_short terms cannot use Japanese reverse fees")
            if (
                self.market == MarginMarket.JP
                and self.reverse_stock_borrow_fee_per_share_day is None
            ):
                raise ValueError(
                    "JP margin_short terms require an explicit reverse borrow fee"
                )
            if self.market == MarginMarket.JP and self.borrow_cost is not None:
                raise ValueError("JP margin_short terms cannot use US borrow cost")
        else:
            raise ValueError("position terms accept only margin_long or margin_short")
        if self.market == MarginMarket.JP and self.currency != "JPY":
            raise ValueError("Japanese margin terms must use JPY")
        if self.market == MarginMarket.US and self.currency != "USD":
            raise ValueError("US margin terms must use USD")
        return self


class MarginEntryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str = Field(min_length=1, max_length=64)
    symbol: str = Field(pattern=r"^[A-Z0-9.\-]{1,32}$")
    mode: TradeMode
    decision_at: datetime
    entry_at: datetime
    stop_price: float = Field(gt=0)
    take_profit_price: float = Field(gt=0)
    expected_holding_days: int = Field(gt=0)
    sector: str = Field(default="unknown", min_length=1, max_length=128)
    correlation_group: str = Field(
        default="unclassified",
        min_length=1,
        max_length=128,
    )
    requested_quantity: int | None = Field(default=None, gt=0)
    human_review_approved: bool = False

    @model_validator(mode="after")
    def validate_plan(self):
        for timestamp in (self.decision_at, self.entry_at):
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("entry plan timestamps must be timezone-aware")
        if self.entry_at <= self.decision_at:
            raise ValueError("entry_at must be after decision_at")
        if self.mode not in {TradeMode.MARGIN_LONG, TradeMode.MARGIN_SHORT}:
            raise ValueError("entry plan accepts only margin_long or margin_short")
        return self


class MarginOhlcBar(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str = Field(pattern=r"^[A-Z0-9.\-]{1,32}$")
    price_time: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float | None = Field(default=None, ge=0)
    fx_rate_to_account: float = Field(default=1.0, gt=0)
    tradable: bool = True
    suspended: bool = False
    limit_up: bool = False
    limit_down: bool = False
    special_quote: bool = False

    @model_validator(mode="after")
    def validate_bar(self):
        if self.price_time.tzinfo is None or self.price_time.utcoffset() is None:
            raise ValueError("price_time must be timezone-aware")
        if self.high < max(self.open, self.close):
            raise ValueError("high must be at least open and close")
        if self.low > min(self.open, self.close):
            raise ValueError("low must be at most open and close")
        if self.high < self.low:
            raise ValueError("high cannot be below low")
        return self


class MarginSizingResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    quantity: int = Field(ge=0)
    binding_limit: str
    reason_codes: tuple[str, ...] = ()
    risk_limited_quantity: int = Field(ge=0)
    margin_limited_quantity: int = Field(ge=0)
    liquidity_limited_quantity: int = Field(ge=0)
    position_limited_quantity: int = Field(ge=0)
    sector_limited_quantity: int = Field(ge=0)
    correlation_limited_quantity: int = Field(ge=0)
    leverage_limited_quantity: int = Field(ge=0)
    requested_limited_quantity: int | None = Field(default=None, ge=0)


class MarginPositionState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    position_id: str
    asset_id: str
    symbol: str
    mode: TradeMode
    sector: str
    correlation_group: str
    currency: str
    quantity: int = Field(gt=0)
    opened_at: datetime
    decision_at: datetime
    entry_price: float = Field(gt=0)
    entry_fx_rate: float = Field(gt=0)
    stop_price: float = Field(gt=0)
    take_profit_price: float = Field(gt=0)
    entry_notional: float = Field(gt=0)
    margin_reserved: float = Field(gt=0)
    entry_fee: float = Field(ge=0)
    planned_loss: float = Field(ge=0)
    accrued_financing_cost: float = Field(default=0.0, ge=0)
    last_accrual_at: datetime
    held_sessions: int = Field(default=0, ge=0)
    expected_holding_days: int = Field(gt=0)
    repayment_deadline: datetime
    last_mark_price: float = Field(gt=0)
    last_fx_rate: float = Field(gt=0)
    forced_liquidation_pending_reason: str | None = None
    exit_pending_reason: str | None = None
    analysis_input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    analysis_rule_version: str
    margin_terms: MarginPositionTerms
    engine_version: str = MARGIN_POSITION_ENGINE_VERSION


class MarginAccountState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_name: str = Field(min_length=1, max_length=64)
    account_currency: str = Field(default="JPY", pattern=r"^[A-Z]{3}$")
    initial_cash: float = Field(gt=0)
    available_cash: float
    realized_pnl: float = 0.0
    financing_cost_paid: float = Field(default=0.0, ge=0)
    positions: tuple[MarginPositionState, ...] = ()
    events: tuple[dict, ...] = ()
    state_as_of: datetime | None = None
    engine_version: str = MARGIN_POSITION_ENGINE_VERSION
    research_only: bool = True


class MarginEntryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: bool
    account: MarginAccountState
    sizing: MarginSizingResult | None = None
    rejection_codes: tuple[str, ...] = ()
    position_id: str | None = None


class MarginSessionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account: MarginAccountState
    session_events: tuple[dict, ...]
    equity: float
    gross_notional: float
    gross_leverage: float | None
    margin_equity: float
    maintenance_required: float
    maintenance_ratio: float | None
    forced_liquidation_pending: bool
    exit_pending: bool


def margin_position_terms_from_snapshot(
    snapshot: MarginTradingSnapshot,
    mode: TradeMode,
    *,
    terms_basis: MarginTermsBasis,
) -> MarginPositionTerms:
    """Freeze the exact MT-P0 provider values consumed by virtual execution."""

    required = {
        "initial_margin_rate": snapshot.initial_margin_rate,
        "maintenance_margin_rate": snapshot.maintenance_margin_rate,
        "minimum_margin_amount": snapshot.minimum_margin_amount,
        "repayment_term_days": snapshot.repayment_term_days,
        "forced_liquidation_rule_version": (
            snapshot.forced_liquidation_rule_version
        ),
    }
    missing = tuple(name for name, value in required.items() if value is None)
    if missing:
        raise ValueError(
            "margin execution terms are incomplete: " + ", ".join(missing)
        )
    payload = {
        "snapshot": snapshot.model_dump(mode="json"),
        "mode": mode.value,
        "terms_basis": terms_basis.value,
        "engine_version": MARGIN_POSITION_ENGINE_VERSION,
    }
    return MarginPositionTerms(
        provider_record_id=snapshot.provider_record_id,
        input_hash=stable_payload_hash(payload),
        asset_id=snapshot.asset_id,
        symbol=snapshot.symbol,
        source=snapshot.source,
        source_version=snapshot.source_version,
        terms_basis=terms_basis,
        market=snapshot.market,
        currency=snapshot.currency,
        mode=mode,
        initial_margin_rate=float(snapshot.initial_margin_rate),
        maintenance_margin_rate=float(snapshot.maintenance_margin_rate),
        minimum_margin_amount=float(snapshot.minimum_margin_amount),
        margin_interest_rate=(
            snapshot.margin_interest_rate
            if mode == TradeMode.MARGIN_LONG
            else None
        ),
        stock_lending_fee=(
            snapshot.stock_lending_fee
            if mode == TradeMode.MARGIN_SHORT
            else None
        ),
        borrow_cost=(
            snapshot.borrow_cost
            if mode == TradeMode.MARGIN_SHORT
            and snapshot.market == MarginMarket.US
            else None
        ),
        reverse_stock_borrow_fee_per_share_day=(
            snapshot.reverse_stock_borrow_fee
            if mode == TradeMode.MARGIN_SHORT
            and snapshot.market == MarginMarket.JP
            else None
        ),
        repayment_term_days=int(snapshot.repayment_term_days),
        forced_liquidation_rule_version=str(
            snapshot.forced_liquidation_rule_version
        ),
        effective_from=snapshot.effective_from,
        effective_to=snapshot.effective_to,
        available_at=snapshot.available_at,
        fetched_at=snapshot.fetched_at,
    )


def new_margin_account(
    *,
    account_name: str,
    initial_cash: float = 2_500_000,
    account_currency: str = "JPY",
) -> MarginAccountState:
    return MarginAccountState(
        account_name=account_name,
        account_currency=account_currency,
        initial_cash=initial_cash,
        available_cash=initial_cash,
    )


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _round_lot(value: float, lot_size: int) -> int:
    if not math.isfinite(value) or value <= 0:
        return 0
    return int(value // lot_size) * lot_size


def _direction(mode: TradeMode) -> int:
    return 1 if mode == TradeMode.MARGIN_LONG else -1


def _position_mark_values(position: MarginPositionState) -> dict[str, float]:
    current_value = (
        position.last_mark_price * position.quantity * position.last_fx_rate
    )
    entry_value = position.entry_notional
    gross_pnl = _direction(position.mode) * (current_value - entry_value)
    position_margin_equity = (
        position.margin_reserved
        + gross_pnl
        - position.accrued_financing_cost
    )
    maintenance_required = (
        current_value * position.margin_terms.maintenance_margin_rate
    )
    return {
        "current_notional": current_value,
        "gross_pnl": gross_pnl,
        "margin_equity": position_margin_equity,
        "maintenance_required": maintenance_required,
    }


def margin_account_summary(account: MarginAccountState) -> dict[str, float | None]:
    marks = [_position_mark_values(position) for position in account.positions]
    gross_notional = sum(item["current_notional"] for item in marks)
    margin_equity = sum(item["margin_equity"] for item in marks)
    maintenance_required = sum(item["maintenance_required"] for item in marks)
    equity = account.available_cash + margin_equity
    return {
        "equity": equity,
        "gross_notional": gross_notional,
        "gross_leverage": None if equity <= 0 else gross_notional / equity,
        "margin_equity": margin_equity,
        "maintenance_required": maintenance_required,
        "maintenance_ratio": (
            None if gross_notional <= 0 else margin_equity / gross_notional
        ),
    }


def _entry_execution_price(
    mode: TradeMode,
    open_price: float,
    policy: MarginExecutionPolicy,
) -> float:
    adverse_rate = policy.spread_rate / 2 + policy.slippage_rate
    return open_price * (1 + adverse_rate * _direction(mode))


def _exit_execution_price(
    mode: TradeMode,
    reference_price: float,
    policy: MarginExecutionPolicy,
) -> float:
    adverse_rate = policy.spread_rate / 2 + policy.slippage_rate
    return reference_price * (1 - adverse_rate * _direction(mode))


def size_margin_position(
    account: MarginAccountState,
    plan: MarginEntryPlan,
    *,
    entry_price: float,
    entry_fx_rate: float,
    previous_volume: float | None,
    terms: MarginPositionTerms,
    policy: MarginExecutionPolicy,
) -> MarginSizingResult:
    """Use the minimum of risk, margin, liquidity, concentration and leverage."""

    summary = margin_account_summary(account)
    equity = float(summary["equity"])
    if equity <= 0:
        return MarginSizingResult(
            quantity=0,
            binding_limit="account_equity_non_positive",
            reason_codes=("account_equity_non_positive",),
            risk_limited_quantity=0,
            margin_limited_quantity=0,
            liquidity_limited_quantity=0,
            position_limited_quantity=0,
            sector_limited_quantity=0,
            correlation_limited_quantity=0,
            leverage_limited_quantity=0,
        )
    if previous_volume is None or not math.isfinite(previous_volume) or previous_volume <= 0:
        return MarginSizingResult(
            quantity=0,
            binding_limit="previous_volume_missing",
            reason_codes=("previous_volume_missing",),
            risk_limited_quantity=0,
            margin_limited_quantity=0,
            liquidity_limited_quantity=0,
            position_limited_quantity=0,
            sector_limited_quantity=0,
            correlation_limited_quantity=0,
            leverage_limited_quantity=0,
        )

    unit_notional = entry_price * entry_fx_rate
    unit_entry_fee = unit_notional * policy.fee_rate
    unit_stop_value = plan.stop_price * entry_fx_rate
    unit_risk = abs(unit_notional - unit_stop_value) + (
        unit_notional * (policy.fee_rate + policy.spread_rate + policy.slippage_rate)
    )
    existing_planned_risk = sum(position.planned_loss for position in account.positions)
    remaining_open_risk = max(
        0.0,
        equity * policy.maximum_total_open_risk_rate - existing_planned_risk,
    )
    risk_budget = min(
        equity * policy.maximum_risk_per_trade_rate,
        remaining_open_risk,
    )
    risk_quantity = _round_lot(risk_budget / unit_risk, policy.lot_size)
    cash_per_unit = unit_notional * terms.initial_margin_rate + unit_entry_fee
    margin_quantity = _round_lot(
        max(0.0, account.available_cash) / cash_per_unit,
        policy.lot_size,
    )
    liquidity_quantity = _round_lot(
        previous_volume * policy.maximum_volume_participation,
        policy.lot_size,
    )
    position_quantity = _round_lot(
        equity * policy.maximum_position_notional_rate / unit_notional,
        policy.lot_size,
    )
    existing_sector_notional = sum(
        _position_mark_values(position)["current_notional"]
        for position in account.positions
        if position.sector == plan.sector
    )
    sector_quantity = _round_lot(
        max(
            0.0,
            equity * policy.maximum_sector_notional_rate
            - existing_sector_notional,
        )
        / unit_notional,
        policy.lot_size,
    )
    existing_correlation_notional = sum(
        _position_mark_values(position)["current_notional"]
        for position in account.positions
        if position.correlation_group == plan.correlation_group
    )
    correlation_quantity = _round_lot(
        max(
            0.0,
            equity * policy.maximum_correlation_notional_rate
            - existing_correlation_notional,
        )
        / unit_notional,
        policy.lot_size,
    )
    leverage_quantity = _round_lot(
        max(
            0.0,
            equity * policy.maximum_gross_leverage
            - float(summary["gross_notional"]),
        )
        / unit_notional,
        policy.lot_size,
    )
    capacities = {
        "risk": risk_quantity,
        "margin": margin_quantity,
        "liquidity": liquidity_quantity,
        "position": position_quantity,
        "sector": sector_quantity,
        "correlation": correlation_quantity,
        "leverage": leverage_quantity,
    }
    requested_quantity = None
    if plan.requested_quantity is not None:
        requested_quantity = _round_lot(plan.requested_quantity, policy.lot_size)
        capacities["requested"] = requested_quantity
    binding_limit, quantity = min(capacities.items(), key=lambda item: item[1])
    reasons = () if quantity > 0 else (f"{binding_limit}_capacity_exhausted",)
    return MarginSizingResult(
        quantity=quantity,
        binding_limit=binding_limit,
        reason_codes=reasons,
        risk_limited_quantity=risk_quantity,
        margin_limited_quantity=margin_quantity,
        liquidity_limited_quantity=liquidity_quantity,
        position_limited_quantity=position_quantity,
        sector_limited_quantity=sector_quantity,
        correlation_limited_quantity=correlation_quantity,
        leverage_limited_quantity=leverage_quantity,
        requested_limited_quantity=requested_quantity,
    )


def _event(
    event_type: MarginEventType,
    *,
    event_at: datetime,
    symbol: str | None,
    details: dict,
) -> dict:
    payload = {
        "event_type": event_type.value,
        "event_at": _utc(event_at).isoformat(),
        "symbol": symbol,
        "engine_version": MARGIN_POSITION_ENGINE_VERSION,
        "details": details,
    }
    return {**payload, "event_id": stable_payload_hash(payload)}


def _reject_entry(
    account: MarginAccountState,
    plan: MarginEntryPlan,
    codes: tuple[str, ...],
) -> MarginEntryResult:
    event = _event(
        MarginEventType.REJECTED,
        event_at=plan.entry_at,
        symbol=plan.symbol,
        details={"reason_codes": list(codes), "mode": plan.mode.value},
    )
    return MarginEntryResult(
        accepted=False,
        account=account.model_copy(update={"events": (*account.events, event)}),
        rejection_codes=codes,
    )


def open_margin_position(
    account: MarginAccountState,
    plan: MarginEntryPlan,
    bar: MarginOhlcBar,
    *,
    previous_volume: float | None,
    terms: MarginPositionTerms,
    analysis_card: MarginAnalysisCard,
    policy: MarginExecutionPolicy | None = None,
) -> MarginEntryResult:
    """Open a virtual position after rechecking the frozen analysis boundary."""

    selected_policy = policy or MarginExecutionPolicy()
    if plan.symbol != bar.symbol or plan.symbol != analysis_card.symbol:
        raise ValueError("entry plan, bar and analysis card symbols must match")
    if (
        plan.asset_id != analysis_card.asset_id
        or plan.asset_id != terms.asset_id
        or plan.symbol != terms.symbol
    ):
        raise ValueError("entry plan, terms and analysis card identity must match")
    if plan.mode != terms.mode or plan.mode != analysis_card.mode:
        raise ValueError("entry plan, terms and analysis card modes must match")
    if analysis_card.market != terms.market:
        raise ValueError("margin terms market must match the analysis card")
    if _utc(analysis_card.as_of) != _utc(plan.decision_at):
        raise ValueError("analysis card as_of must match decision_at")
    if _utc(analysis_card.data_as_of) > _utc(plan.decision_at):
        return _reject_entry(account, plan, ("analysis_data_not_known_at_decision",))
    if (
        analysis_card.provider_record_id is not None
        and analysis_card.provider_record_id != terms.provider_record_id
    ):
        raise ValueError("margin terms must match the analyzed provider record")
    if _utc(bar.price_time) != _utc(plan.entry_at):
        raise ValueError("entry bar must match entry_at")
    if account.state_as_of is not None and _utc(plan.entry_at) <= _utc(account.state_as_of):
        raise ValueError("account state must advance forward in time")
    cutoff = _utc(plan.decision_at)
    if terms.available_at > cutoff or terms.fetched_at > cutoff:
        return _reject_entry(account, plan, ("margin_terms_not_known_at_decision",))
    if terms.effective_from > cutoff or (
        terms.effective_to is not None and cutoff >= terms.effective_to
    ):
        return _reject_entry(account, plan, ("margin_terms_not_effective",))
    if analysis_card.analysis_status in {
        MarginAnalysisStatus.BLOCKED,
        MarginAnalysisStatus.NOT_ELIGIBLE,
        MarginAnalysisStatus.INSUFFICIENT_DATA,
    }:
        return _reject_entry(account, plan, ("margin_analysis_not_eligible",))
    if (
        analysis_card.analysis_status == MarginAnalysisStatus.WARNING
        and not plan.human_review_approved
    ):
        return _reject_entry(account, plan, ("margin_analysis_review_not_approved",))
    if analysis_card.hard_block_codes:
        return _reject_entry(account, plan, ("margin_analysis_hard_block",))
    account_summary = margin_account_summary(account)
    if any(
        position.forced_liquidation_pending_reason is not None
        for position in account.positions
    ):
        return _reject_entry(account, plan, ("forced_liquidation_pending",))
    if account.positions and account_summary["maintenance_ratio"] is not None:
        highest_rate = max(
            position.margin_terms.maintenance_margin_rate
            for position in account.positions
        )
        if float(account_summary["maintenance_ratio"]) <= (
            highest_rate + selected_policy.maintenance_warning_buffer
        ):
            return _reject_entry(account, plan, ("account_maintenance_headroom_low",))
    if len(account.positions) >= selected_policy.maximum_positions:
        return _reject_entry(account, plan, ("maximum_positions_reached",))
    if any(position.symbol == plan.symbol for position in account.positions):
        return _reject_entry(account, plan, ("symbol_already_held",))
    if not bar.tradable or bar.suspended or bar.special_quote:
        return _reject_entry(account, plan, ("entry_not_tradable",))
    if plan.mode == TradeMode.MARGIN_LONG and bar.limit_up:
        return _reject_entry(account, plan, ("margin_long_limit_up_no_fill",))
    if plan.mode == TradeMode.MARGIN_SHORT and bar.limit_down:
        return _reject_entry(account, plan, ("margin_short_limit_down_no_fill",))

    entry_price = _entry_execution_price(plan.mode, bar.open, selected_policy)
    if plan.mode == TradeMode.MARGIN_LONG:
        if not plan.stop_price < entry_price < plan.take_profit_price:
            return _reject_entry(account, plan, ("invalid_margin_long_exit_levels",))
    elif not plan.take_profit_price < entry_price < plan.stop_price:
        return _reject_entry(account, plan, ("invalid_margin_short_exit_levels",))
    sizing = size_margin_position(
        account,
        plan,
        entry_price=entry_price,
        entry_fx_rate=bar.fx_rate_to_account,
        previous_volume=previous_volume,
        terms=terms,
        policy=selected_policy,
    )
    if sizing.quantity <= 0:
        return MarginEntryResult(
            accepted=False,
            account=_reject_entry(account, plan, sizing.reason_codes).account,
            sizing=sizing,
            rejection_codes=sizing.reason_codes,
        )
    quantity = sizing.quantity
    entry_notional = entry_price * quantity * bar.fx_rate_to_account
    margin_reserved = max(
        entry_notional * terms.initial_margin_rate,
        terms.minimum_margin_amount * bar.fx_rate_to_account,
    )
    entry_fee = entry_notional * selected_policy.fee_rate
    cash_required = margin_reserved + entry_fee
    if cash_required > account.available_cash:
        return _reject_entry(account, plan, ("margin_cash_requirement_exceeded",))
    planned_loss = quantity * (
        abs(entry_price - plan.stop_price) * bar.fx_rate_to_account
        + entry_price
        * bar.fx_rate_to_account
        * (
            selected_policy.fee_rate
            + selected_policy.spread_rate
            + selected_policy.slippage_rate
        )
    )
    position_id = stable_payload_hash(
        {
            "account": account.account_name,
            "asset_id": plan.asset_id,
            "symbol": plan.symbol,
            "mode": plan.mode.value,
            "entry_at": plan.entry_at,
            "analysis_input_hash": analysis_card.input_hash,
            "margin_terms_input_hash": terms.input_hash,
            "policy_version": selected_policy.version,
        }
    )
    position = MarginPositionState(
        position_id=position_id,
        asset_id=plan.asset_id,
        symbol=plan.symbol,
        mode=plan.mode,
        sector=plan.sector,
        correlation_group=plan.correlation_group,
        currency=terms.currency,
        quantity=quantity,
        opened_at=_utc(plan.entry_at),
        decision_at=_utc(plan.decision_at),
        entry_price=entry_price,
        entry_fx_rate=bar.fx_rate_to_account,
        stop_price=plan.stop_price,
        take_profit_price=plan.take_profit_price,
        entry_notional=entry_notional,
        margin_reserved=margin_reserved,
        entry_fee=entry_fee,
        planned_loss=planned_loss,
        last_accrual_at=_utc(plan.entry_at),
        expected_holding_days=plan.expected_holding_days,
        repayment_deadline=_utc(plan.entry_at)
        + timedelta(days=terms.repayment_term_days),
        last_mark_price=entry_price,
        last_fx_rate=bar.fx_rate_to_account,
        analysis_input_hash=analysis_card.input_hash,
        analysis_rule_version=analysis_card.risk_rule_version,
        margin_terms=terms,
    )
    event = _event(
        MarginEventType.ENTRY,
        event_at=plan.entry_at,
        symbol=plan.symbol,
        details={
            "position_id": position_id,
            "mode": plan.mode.value,
            "quantity": quantity,
            "requested_quantity": plan.requested_quantity,
            "partial_fill": (
                plan.requested_quantity is not None
                and quantity < plan.requested_quantity
            ),
            "entry_price": entry_price,
            "entry_notional": entry_notional,
            "margin_reserved": margin_reserved,
            "minimum_margin_amount": terms.minimum_margin_amount,
            "entry_fee": entry_fee,
            "analysis_input_hash": analysis_card.input_hash,
            "margin_terms_input_hash": terms.input_hash,
            "policy_version": selected_policy.version,
            "real_order_sent": False,
        },
    )
    updated = account.model_copy(
        update={
            "available_cash": account.available_cash - cash_required,
            "positions": (*account.positions, position),
            "events": (*account.events, event),
            "state_as_of": _utc(plan.entry_at),
            "research_only": (
                account.research_only
                or terms.terms_basis == MarginTermsBasis.VERSIONED_RESEARCH_PROXY
            ),
        }
    )
    return MarginEntryResult(
        accepted=True,
        account=updated,
        sizing=sizing,
        position_id=position_id,
    )


def _financing_cost(
    position: MarginPositionState,
    *,
    through: datetime,
    fx_rate: float,
    policy: MarginExecutionPolicy,
    terms: MarginPositionTerms | None = None,
    from_time: datetime | None = None,
) -> tuple[float, int]:
    start = from_time or position.last_accrual_at
    days = max(0, (_utc(through).date() - _utc(start).date()).days)
    if days == 0:
        return 0.0, 0
    selected_terms = terms or position.margin_terms
    if position.mode == TradeMode.MARGIN_LONG:
        annualized_rate = float(selected_terms.margin_interest_rate)
    else:
        annualized_rate = float(selected_terms.stock_lending_fee) + float(
            selected_terms.borrow_cost or 0.0
        )
    annualized_cost = (
        position.entry_notional
        * annualized_rate
        * days
        / policy.annual_day_count
    )
    reverse_fee = 0.0
    if position.mode == TradeMode.MARGIN_SHORT:
        reverse_fee = (
            float(selected_terms.reverse_stock_borrow_fee_per_share_day or 0.0)
            * position.quantity
            * fx_rate
            * days
        )
    return annualized_cost + reverse_fee, days


def _validate_terms_update(
    position: MarginPositionState,
    terms: MarginPositionTerms,
    *,
    session_at: datetime,
) -> None:
    if (
        terms.asset_id != position.asset_id
        or terms.symbol != position.symbol
        or terms.mode != position.mode
        or terms.currency != position.currency
        or terms.market != position.margin_terms.market
    ):
        raise ValueError("updated margin terms do not match the open position")
    if terms.source != position.margin_terms.source:
        raise ValueError("margin terms source cannot change within an open position")
    cutoff = _utc(session_at)
    if terms.available_at > cutoff or terms.fetched_at > cutoff:
        raise ValueError("updated margin terms were not known by the session")
    if terms.effective_from > cutoff or (
        terms.effective_to is not None and cutoff >= terms.effective_to
    ):
        raise ValueError("updated margin terms are not effective for the session")
    if terms.effective_from < position.margin_terms.effective_from:
        raise ValueError("updated margin terms cannot move backward in effective time")


def _session_financing_cost(
    position: MarginPositionState,
    *,
    through: datetime,
    fx_rate: float,
    policy: MarginExecutionPolicy,
    terms_update: MarginPositionTerms | None,
) -> tuple[MarginPositionState, float, int]:
    if terms_update is None or terms_update.input_hash == position.margin_terms.input_hash:
        cost, days = _financing_cost(
            position,
            through=through,
            fx_rate=fx_rate,
            policy=policy,
        )
        return position, cost, days
    _validate_terms_update(position, terms_update, session_at=through)
    transition_at = max(
        _utc(position.last_accrual_at),
        _utc(terms_update.effective_from),
    )
    old_cost, old_days = _financing_cost(
        position,
        through=transition_at,
        fx_rate=fx_rate,
        policy=policy,
    )
    new_cost, new_days = _financing_cost(
        position,
        through=through,
        fx_rate=fx_rate,
        policy=policy,
        terms=terms_update,
        from_time=transition_at,
    )
    updated = position.model_copy(
        update={
            "margin_terms": terms_update,
            "repayment_deadline": min(
                _utc(position.repayment_deadline),
                _utc(position.opened_at)
                + timedelta(days=terms_update.repayment_term_days),
            ),
        }
    )
    return updated, old_cost + new_cost, old_days + new_days


def _can_close(position: MarginPositionState, bar: MarginOhlcBar) -> bool:
    if not bar.tradable or bar.suspended or bar.special_quote:
        return False
    if position.mode == TradeMode.MARGIN_LONG and bar.limit_down:
        return False
    if position.mode == TradeMode.MARGIN_SHORT and bar.limit_up:
        return False
    return True


def _close_position(
    account: MarginAccountState,
    position: MarginPositionState,
    *,
    reference_price: float,
    fx_rate: float,
    event_at: datetime,
    reason: str,
    policy: MarginExecutionPolicy,
    accrued_cost: float,
) -> tuple[MarginAccountState, dict]:
    execution_price = _exit_execution_price(position.mode, reference_price, policy)
    exit_value = execution_price * position.quantity * fx_rate
    gross_pnl = _direction(position.mode) * (exit_value - position.entry_notional)
    exit_fee = exit_value * policy.fee_rate
    total_financing = position.accrued_financing_cost + accrued_cost
    net_trade_pnl = gross_pnl - position.entry_fee - exit_fee - total_financing
    released_cash = position.margin_reserved + gross_pnl - exit_fee - total_financing
    event = _event(
        MarginEventType.EXIT,
        event_at=event_at,
        symbol=position.symbol,
        details={
            "position_id": position.position_id,
            "mode": position.mode.value,
            "quantity": position.quantity,
            "execution_price": execution_price,
            "gross_pnl": gross_pnl,
            "entry_fee": position.entry_fee,
            "exit_fee": exit_fee,
            "financing_cost": total_financing,
            "margin_terms_input_hash": position.margin_terms.input_hash,
            "realized_pnl": net_trade_pnl,
            "reason": reason,
            "real_order_sent": False,
        },
    )
    remaining = tuple(
        item for item in account.positions if item.position_id != position.position_id
    )
    updated = account.model_copy(
        update={
            "available_cash": account.available_cash + released_cash,
            "realized_pnl": account.realized_pnl + net_trade_pnl,
            "financing_cost_paid": account.financing_cost_paid + total_financing,
            "positions": remaining,
            "events": (*account.events, event),
            "state_as_of": _utc(event_at),
        }
    )
    return updated, event


def _regular_exit(
    position: MarginPositionState,
    bar: MarginOhlcBar,
) -> tuple[float | None, str | None]:
    if position.mode == TradeMode.MARGIN_LONG:
        if bar.open <= position.stop_price:
            return bar.open, "stop_loss_gap"
        if bar.open >= position.take_profit_price:
            return bar.open, "take_profit_gap"
        if bar.low <= position.stop_price:
            return position.stop_price, "stop_loss"
        if bar.high >= position.take_profit_price:
            return position.take_profit_price, "take_profit"
    else:
        if bar.open >= position.stop_price:
            return bar.open, "stop_loss_gap"
        if bar.open <= position.take_profit_price:
            return bar.open, "take_profit_gap"
        if bar.high >= position.stop_price:
            return position.stop_price, "stop_loss"
        if bar.low <= position.take_profit_price:
            return position.take_profit_price, "take_profit"
    return None, None


def advance_margin_account_session(
    account: MarginAccountState,
    bars: tuple[MarginOhlcBar, ...],
    *,
    policy: MarginExecutionPolicy | None = None,
    terms_updates: tuple[MarginPositionTerms, ...] = (),
) -> MarginSessionResult:
    """Advance positions once, scheduling maintenance liquidation for next open."""

    selected_policy = policy or MarginExecutionPolicy()
    if not bars:
        raise ValueError("at least one OHLC bar is required")
    session_times = {_utc(bar.price_time) for bar in bars}
    if len(session_times) != 1:
        raise ValueError("all bars must belong to the same session timestamp")
    session_at = next(iter(session_times))
    if account.state_as_of is not None and session_at <= _utc(account.state_as_of):
        raise ValueError("margin account sessions must move forward")
    by_symbol = {bar.symbol: bar for bar in bars}
    if len(by_symbol) != len(bars):
        raise ValueError("duplicate OHLC bars for the same symbol")
    terms_by_symbol = {terms.symbol: terms for terms in terms_updates}
    if len(terms_by_symbol) != len(terms_updates):
        raise ValueError("duplicate margin terms updates for the same symbol")
    open_symbols = {position.symbol for position in account.positions}
    unknown_terms_symbols = set(terms_by_symbol).difference(open_symbols)
    if unknown_terms_symbols:
        raise ValueError(
            "margin terms update has no open position: "
            + ", ".join(sorted(unknown_terms_symbols))
        )
    working = account
    session_events: list[dict] = []

    for original in tuple(working.positions):
        position = next(
            (
                item
                for item in working.positions
                if item.position_id == original.position_id
            ),
            None,
        )
        if position is None:
            continue
        bar = by_symbol.get(position.symbol)
        if bar is None:
            raise ValueError(f"missing OHLC bar for open margin position {position.symbol}")
        position, accrued, days = _session_financing_cost(
            position,
            through=session_at,
            fx_rate=bar.fx_rate_to_account,
            policy=selected_policy,
            terms_update=terms_by_symbol.get(position.symbol),
        )
        pending_reason = position.forced_liquidation_pending_reason
        if session_at >= _utc(position.repayment_deadline):
            pending_reason = pending_reason or "repayment_deadline_reached"
        exit_pending_reason = position.exit_pending_reason
        if pending_reason is not None or exit_pending_reason is not None:
            close_reason = (
                f"forced_liquidation:{pending_reason}"
                if pending_reason is not None
                else f"deferred_exit:{exit_pending_reason}"
            )
            if _can_close(position, bar):
                working, event = _close_position(
                    working,
                    position,
                    reference_price=bar.open,
                    fx_rate=bar.fx_rate_to_account,
                    event_at=session_at,
                    reason=close_reason,
                    policy=selected_policy,
                    accrued_cost=accrued,
                )
                session_events.append(event)
                continue
            updated_position = position.model_copy(
                update={
                    "accrued_financing_cost": (
                        position.accrued_financing_cost + accrued
                    ),
                    "last_accrual_at": session_at,
                    "last_mark_price": bar.close,
                    "last_fx_rate": bar.fx_rate_to_account,
                    "held_sessions": position.held_sessions + 1,
                    "forced_liquidation_pending_reason": pending_reason,
                    "exit_pending_reason": exit_pending_reason,
                }
            )
            event = _event(
                (
                    MarginEventType.FORCED_LIQUIDATION_DEFERRED
                    if pending_reason is not None
                    else MarginEventType.EXIT_DEFERRED
                ),
                event_at=session_at,
                symbol=position.symbol,
                details={
                    "position_id": position.position_id,
                    "reason": pending_reason or exit_pending_reason,
                    "limit_up": bar.limit_up,
                    "limit_down": bar.limit_down,
                    "suspended": bar.suspended,
                    "special_quote": bar.special_quote,
                    "real_order_sent": False,
                },
            )
            working = working.model_copy(
                update={
                    "positions": tuple(
                        updated_position
                        if item.position_id == position.position_id
                        else item
                        for item in working.positions
                    ),
                    "events": (*working.events, event),
                }
            )
            session_events.append(event)
            continue

        exit_reference, reason = _regular_exit(position, bar)
        if exit_reference is not None and reason is not None and _can_close(position, bar):
            working, event = _close_position(
                working,
                position,
                reference_price=exit_reference,
                fx_rate=bar.fx_rate_to_account,
                event_at=session_at,
                reason=reason,
                policy=selected_policy,
                accrued_cost=accrued,
            )
            session_events.append(event)
            continue
        if exit_reference is not None and reason is not None:
            updated_position = position.model_copy(
                update={
                    "accrued_financing_cost": (
                        position.accrued_financing_cost + accrued
                    ),
                    "last_accrual_at": session_at,
                    "last_mark_price": bar.close,
                    "last_fx_rate": bar.fx_rate_to_account,
                    "held_sessions": position.held_sessions + 1,
                    "exit_pending_reason": reason,
                }
            )
            event = _event(
                MarginEventType.EXIT_DEFERRED,
                event_at=session_at,
                symbol=position.symbol,
                details={
                    "position_id": position.position_id,
                    "reason": reason,
                    "accrued_financing_cost": accrued,
                    "limit_up": bar.limit_up,
                    "limit_down": bar.limit_down,
                    "suspended": bar.suspended,
                    "special_quote": bar.special_quote,
                    "real_order_sent": False,
                },
            )
            working = working.model_copy(
                update={
                    "positions": tuple(
                        updated_position
                        if item.position_id == position.position_id
                        else item
                        for item in working.positions
                    ),
                    "events": (*working.events, event),
                }
            )
            session_events.append(event)
            continue

        updated_position = position.model_copy(
            update={
                "accrued_financing_cost": position.accrued_financing_cost + accrued,
                "last_accrual_at": session_at,
                "last_mark_price": bar.close,
                "last_fx_rate": bar.fx_rate_to_account,
                "held_sessions": position.held_sessions + 1,
            }
        )
        accrual_event = _event(
            MarginEventType.FINANCING_ACCRUAL,
            event_at=session_at,
            symbol=position.symbol,
            details={
                "position_id": position.position_id,
                "days": days,
                "cost": accrued,
                "cumulative_cost": updated_position.accrued_financing_cost,
                "margin_terms_input_hash": updated_position.margin_terms.input_hash,
            },
        )
        working = working.model_copy(
            update={
                "positions": tuple(
                    updated_position
                    if item.position_id == position.position_id
                    else item
                    for item in working.positions
                ),
                "events": (*working.events, accrual_event),
            }
        )
        session_events.append(accrual_event)

    summary = margin_account_summary(working)
    if working.positions and float(summary["margin_equity"]) <= float(
        summary["maintenance_required"]
    ):
        scheduled: list[MarginPositionState] = []
        for position in working.positions:
            if position.forced_liquidation_pending_reason is not None:
                scheduled.append(position)
                continue
            reason = "maintenance_margin_breach"
            scheduled.append(
                position.model_copy(
                    update={"forced_liquidation_pending_reason": reason}
                )
            )
            event = _event(
                MarginEventType.FORCED_LIQUIDATION_SCHEDULED,
                event_at=session_at,
                symbol=position.symbol,
                details={
                    "position_id": position.position_id,
                    "reason": reason,
                    "maintenance_ratio": summary["maintenance_ratio"],
                    "maintenance_required": summary["maintenance_required"],
                    "timing": selected_policy.forced_liquidation_timing,
                    "real_order_sent": False,
                },
            )
            session_events.append(event)
            working = working.model_copy(update={"events": (*working.events, event)})
        working = working.model_copy(update={"positions": tuple(scheduled)})
    elif working.positions and summary["maintenance_ratio"] is not None:
        highest_rate = max(
            position.margin_terms.maintenance_margin_rate
            for position in working.positions
        )
        if float(summary["maintenance_ratio"]) <= (
            highest_rate + selected_policy.maintenance_warning_buffer
        ):
            event = _event(
                MarginEventType.MAINTENANCE_WARNING,
                event_at=session_at,
                symbol=None,
                details={
                    "maintenance_ratio": summary["maintenance_ratio"],
                    "warning_threshold": (
                        highest_rate + selected_policy.maintenance_warning_buffer
                    ),
                },
            )
            session_events.append(event)
            working = working.model_copy(update={"events": (*working.events, event)})

    working = working.model_copy(update={"state_as_of": session_at})
    final_summary = margin_account_summary(working)
    return MarginSessionResult(
        account=working,
        session_events=tuple(session_events),
        equity=float(final_summary["equity"]),
        gross_notional=float(final_summary["gross_notional"]),
        gross_leverage=final_summary["gross_leverage"],
        margin_equity=float(final_summary["margin_equity"]),
        maintenance_required=float(final_summary["maintenance_required"]),
        maintenance_ratio=final_summary["maintenance_ratio"],
        forced_liquidation_pending=any(
            position.forced_liquidation_pending_reason is not None
            for position in working.positions
        ),
        exit_pending=any(
            position.exit_pending_reason is not None
            for position in working.positions
        ),
    )
