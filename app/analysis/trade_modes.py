"""Trade-mode vocabulary and conservative margin-data readiness checks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.providers.margin import MarginDataQuality, MarginTradingSnapshot, ShortAvailability


TRADE_MODE_BOUNDARY_VERSION = "trade-mode-boundary-v1"


class TradeMode(StrEnum):
    CASH = "cash"
    MARGIN_LONG = "margin_long"
    MARGIN_SHORT = "margin_short"
    AUTO_SELECT = "auto_select"


class EligibilityStatus(StrEnum):
    ELIGIBLE = "eligible"
    NOT_ELIGIBLE = "not_eligible"
    INSUFFICIENT_DATA = "insufficient_data"


class TradeModeEligibility(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: TradeMode
    status: EligibilityStatus
    reason_codes: tuple[str, ...] = ()
    human_review_required: bool = False
    boundary_version: str = TRADE_MODE_BOUNDARY_VERSION
    provider_record_id: str | None = None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    return value.astimezone(timezone.utc)


def _snapshot_time_reasons(
    snapshot: MarginTradingSnapshot,
    *,
    as_of: datetime,
    maximum_age: timedelta,
) -> list[str]:
    reasons: list[str] = []
    cutoff = _utc(as_of)
    if snapshot.available_at > cutoff:
        reasons.append("margin_data_not_available_as_of_decision")
    if snapshot.fetched_at > cutoff:
        reasons.append("margin_data_fetched_after_decision")
    if snapshot.effective_from > cutoff:
        reasons.append("margin_terms_not_effective_as_of_decision")
    if snapshot.effective_to is not None and cutoff >= snapshot.effective_to:
        reasons.append("margin_terms_expired_as_of_decision")
    if cutoff - snapshot.available_at > maximum_age:
        reasons.append("margin_data_stale")
    if snapshot.data_quality in {
        MarginDataQuality.STALE,
        MarginDataQuality.UNAVAILABLE,
        MarginDataQuality.SYNTHETIC_RESEARCH,
    }:
        reasons.append(f"margin_data_quality_{snapshot.data_quality.value}")
    return reasons


def _missing_long_fields(snapshot: MarginTradingSnapshot) -> list[str]:
    required = {
        "margin_interest_rate": snapshot.margin_interest_rate,
        "initial_margin_rate": snapshot.initial_margin_rate,
        "maintenance_margin_rate": snapshot.maintenance_margin_rate,
        "repayment_term_days": snapshot.repayment_term_days,
    }
    return [f"missing_{name}" for name, value in required.items() if value is None]


def _missing_short_fields(snapshot: MarginTradingSnapshot) -> list[str]:
    required = {
        "stock_lending_fee": snapshot.stock_lending_fee,
        "initial_margin_rate": snapshot.initial_margin_rate,
        "maintenance_margin_rate": snapshot.maintenance_margin_rate,
        "repayment_term_days": snapshot.repayment_term_days,
    }
    reasons = [f"missing_{name}" for name, value in required.items() if value is None]
    if snapshot.market.value == "us" and snapshot.borrow_cost is None:
        reasons.append("missing_borrow_cost")
    return reasons


def assess_trade_mode_data(
    mode: TradeMode | str,
    snapshot: MarginTradingSnapshot | None,
    *,
    as_of: datetime,
    maximum_age: timedelta = timedelta(days=7),
) -> TradeModeEligibility:
    """Assess data readiness only; MT-P2 owns analytical risk decisions."""

    selected_mode = TradeMode(mode)
    _utc(as_of)
    if maximum_age <= timedelta(0):
        raise ValueError("maximum_age must be positive")
    if selected_mode == TradeMode.CASH:
        return TradeModeEligibility(mode=selected_mode, status=EligibilityStatus.ELIGIBLE)
    if selected_mode == TradeMode.AUTO_SELECT:
        return TradeModeEligibility(
            mode=selected_mode,
            status=EligibilityStatus.INSUFFICIENT_DATA,
            reason_codes=("auto_select_strategy_comparison_not_implemented",),
            human_review_required=True,
            provider_record_id=(None if snapshot is None else snapshot.provider_record_id),
        )
    if snapshot is None:
        return TradeModeEligibility(
            mode=selected_mode,
            status=EligibilityStatus.INSUFFICIENT_DATA,
            reason_codes=("margin_snapshot_missing",),
            human_review_required=True,
        )

    reasons = _snapshot_time_reasons(
        snapshot,
        as_of=as_of,
        maximum_age=maximum_age,
    )
    eligible_flag = (
        snapshot.margin_long_eligible
        if selected_mode == TradeMode.MARGIN_LONG
        else snapshot.margin_short_eligible
    )
    if eligible_flag is False and not reasons:
        return TradeModeEligibility(
            mode=selected_mode,
            status=EligibilityStatus.NOT_ELIGIBLE,
            reason_codes=(f"{selected_mode.value}_explicitly_not_eligible",),
            provider_record_id=snapshot.provider_record_id,
        )
    if eligible_flag is None:
        reasons.append(f"{selected_mode.value}_eligibility_unknown")

    if selected_mode == TradeMode.MARGIN_LONG:
        reasons.extend(_missing_long_fields(snapshot))
    else:
        if snapshot.short_availability not in {
            ShortAvailability.AVAILABLE,
            ShortAvailability.LIMITED,
        }:
            reasons.append("short_inventory_not_confirmed")
        reasons.extend(_missing_short_fields(snapshot))

    unique_reasons = tuple(sorted(set(reasons)))
    if unique_reasons:
        return TradeModeEligibility(
            mode=selected_mode,
            status=EligibilityStatus.INSUFFICIENT_DATA,
            reason_codes=unique_reasons,
            human_review_required=True,
            provider_record_id=snapshot.provider_record_id,
        )
    return TradeModeEligibility(
        mode=selected_mode,
        status=EligibilityStatus.ELIGIBLE,
        human_review_required=(
            snapshot.data_quality == MarginDataQuality.PARTIAL
            or snapshot.short_availability == ShortAvailability.LIMITED
        ),
        provider_record_id=snapshot.provider_record_id,
    )


def assess_all_trade_modes(
    snapshot: MarginTradingSnapshot | None,
    *,
    as_of: datetime,
    maximum_age: timedelta = timedelta(days=7),
) -> dict[str, TradeModeEligibility]:
    """Return every mode separately; never hide rejected or unknown candidates."""

    return {
        mode.value: assess_trade_mode_data(
            mode,
            snapshot,
            as_of=as_of,
            maximum_age=maximum_age,
        )
        for mode in TradeMode
    }
