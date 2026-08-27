from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.analysis.margin_risk import (
    EtfLeverageProfile,
    MarginAnalysisInput,
    MarginAnalysisStatus,
    MarginRiskLevel,
    MarginRiskPolicy,
    MarketRegime,
    build_margin_analysis_card,
)
from app.analysis.trade_modes import EligibilityStatus, TradeMode
from app.providers.margin import MarginAssetType, MarginDataQuality, ShortAvailability
from tests.test_trade_modes import NOW, _snapshot


def _inputs(**changes) -> MarginAnalysisInput:
    values = {
        "asset_id": "asset-1",
        "symbol": "1306",
        "market": "jp",
        "asset_type": "etf",
        "currency": "JPY",
        "mode": TradeMode.MARGIN_LONG,
        "as_of": NOW,
        "data_as_of": NOW - timedelta(minutes=30),
        "market_regime": MarketRegime.BULLISH,
        "trend_return_20d": 0.08,
        "volume_ratio_20d": 1.4,
        "average_traded_value_20d": 100_000_000,
        "atr_pct": 0.03,
        "gap_risk_pct": 0.02,
        "support_distance_pct": 0.02,
        "resistance_distance_pct": 0.02,
        "margin_long_balance_change_ratio_20d": 0.05,
        "margin_short_balance_change_ratio_20d": 0.05,
        "risk_reward_ratio": 2.0,
        "days_to_event": 10,
        "expected_holding_days": 20,
        "maintenance_headroom_pct": 0.12,
        "etf_leverage_profile": EtfLeverageProfile.STANDARD,
    }
    values.update(changes)
    return MarginAnalysisInput(**values)


def _codes(card) -> set[str]:
    return {finding.code for finding in card.risk_findings}


def test_margin_long_candidate_has_supporting_factors_but_never_creates_order():
    card = build_margin_analysis_card(_inputs(), _snapshot())

    assert card.eligibility_status == EligibilityStatus.ELIGIBLE
    assert card.analysis_status == MarginAnalysisStatus.CANDIDATE
    assert card.decision == "信用買い候補"
    assert card.risk_level == MarginRiskLevel.MODERATE
    assert card.warning_codes == ()
    assert card.hard_block_codes == ()
    assert "uptrend_confirmed" in card.supporting_factors
    assert "nearby_support_confirmed" in card.supporting_factors
    assert not card.virtual_order_allowed
    assert card.order_generation_status == "not_connected_mt_p2"


def test_margin_short_candidate_uses_downtrend_resistance_and_short_inputs():
    card = build_margin_analysis_card(
        _inputs(
            mode=TradeMode.MARGIN_SHORT,
            market_regime=MarketRegime.BEARISH,
            trend_return_20d=-0.08,
            support_distance_pct=None,
        ),
        _snapshot(),
    )

    assert card.analysis_status == MarginAnalysisStatus.CANDIDATE
    assert card.decision == "信用売り候補"
    assert "downtrend_confirmed" in card.supporting_factors
    assert "nearby_resistance_confirmed" in card.supporting_factors
    assert "short_squeeze_risk_within_policy" in card.supporting_factors


def test_missing_or_stale_margin_data_is_insufficient_and_not_fallback_to_cash():
    missing = build_margin_analysis_card(_inputs(), None)
    stale = build_margin_analysis_card(
        _inputs(),
        _snapshot(
            data_quality=MarginDataQuality.STALE,
            available_at=NOW - timedelta(days=30),
            fetched_at=NOW - timedelta(days=29),
        ),
    )

    assert missing.analysis_status == MarginAnalysisStatus.INSUFFICIENT_DATA
    assert missing.mode == TradeMode.MARGIN_LONG
    assert missing.decision == "データ不足"
    assert "margin_snapshot_missing" in missing.hard_block_codes
    assert stale.analysis_status == MarginAnalysisStatus.INSUFFICIENT_DATA
    assert "margin_data_stale" in stale.hard_block_codes
    assert not missing.virtual_order_allowed
    assert not stale.virtual_order_allowed


def test_explicit_provider_ineligibility_is_distinct_from_missing_data():
    card = build_margin_analysis_card(
        _inputs(mode=TradeMode.MARGIN_SHORT, market_regime="bearish", trend_return_20d=-0.1),
        _snapshot(margin_short_eligible=False),
    )

    assert card.eligibility_status == EligibilityStatus.NOT_ELIGIBLE
    assert card.analysis_status == MarginAnalysisStatus.NOT_ELIGIBLE
    assert card.decision == "信用取引不可"
    assert "margin_short_explicitly_not_eligible" in card.hard_block_codes


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"trend_return_20d": -0.01}, "long_trend_not_confirmed"),
        ({"average_traded_value_20d": 49_999_999}, "liquidity_below_minimum"),
        ({"risk_reward_ratio": 1.49}, "risk_reward_below_minimum"),
        ({"days_to_event": 2}, "event_imminent"),
        ({"atr_pct": 0.12}, "extreme_volatility"),
        ({"gap_risk_pct": 0.08}, "extreme_gap_risk"),
        ({"maintenance_headroom_pct": 0.0}, "maintenance_headroom_exhausted"),
    ],
)
def test_common_hard_gates_block_candidate(changes, reason):
    card = build_margin_analysis_card(_inputs(**changes), _snapshot())

    assert card.analysis_status == MarginAnalysisStatus.BLOCKED
    assert card.risk_level == MarginRiskLevel.BLOCKED
    assert reason in card.hard_block_codes
    assert not card.virtual_order_allowed


def test_required_market_fields_are_not_guessed_when_missing():
    card = build_margin_analysis_card(
        _inputs(
            volume_ratio_20d=None,
            atr_pct=None,
            risk_reward_ratio=None,
            days_to_event=None,
        ),
        _snapshot(),
    )

    assert card.analysis_status == MarginAnalysisStatus.BLOCKED
    assert {
        "missing_volume_ratio_20d",
        "missing_atr_pct",
        "missing_risk_reward_ratio",
        "missing_days_to_event",
    }.issubset(_codes(card))


def test_warnings_require_review_without_becoming_a_candidate_order():
    card = build_margin_analysis_card(
        _inputs(
            volume_ratio_20d=0.8,
            atr_pct=0.06,
            gap_risk_pct=0.04,
            days_to_event=4,
            maintenance_headroom_pct=0.04,
        ),
        _snapshot(),
    )

    assert card.analysis_status == MarginAnalysisStatus.WARNING
    assert card.risk_level == MarginRiskLevel.HIGH
    assert {
        "volume_not_expanding",
        "high_volatility",
        "high_gap_risk",
        "event_near",
        "maintenance_headroom_low",
    }.issubset(_codes(card))
    assert card.human_review_required
    assert not card.virtual_order_allowed


def test_directional_margin_crowding_has_separate_long_and_short_gates():
    crowded_long = build_margin_analysis_card(
        _inputs(),
        _snapshot(lending_ratio=10.0),
    )
    crowded_short = build_margin_analysis_card(
        _inputs(
            mode="margin_short",
            market_regime="bearish",
            trend_return_20d=-0.05,
        ),
        _snapshot(lending_ratio=0.2),
    )

    assert "margin_long_crowding_extreme" in crowded_long.hard_block_codes
    assert "short_squeeze_risk_extreme" in crowded_short.hard_block_codes


def test_directional_margin_balance_growth_is_not_inferred_from_one_snapshot():
    missing = build_margin_analysis_card(
        _inputs(margin_long_balance_change_ratio_20d=None),
        _snapshot(),
    )
    crowded_long = build_margin_analysis_card(
        _inputs(margin_long_balance_change_ratio_20d=0.50),
        _snapshot(),
    )
    crowded_short = build_margin_analysis_card(
        _inputs(
            mode="margin_short",
            market_regime="bearish",
            trend_return_20d=-0.05,
            margin_short_balance_change_ratio_20d=0.50,
        ),
        _snapshot(),
    )

    assert "margin_balance_change_missing" in missing.warning_codes
    assert "margin_long_balance_growth_extreme" in crowded_long.hard_block_codes
    assert "margin_short_balance_growth_extreme" in crowded_short.hard_block_codes


def test_financing_term_and_cost_are_gated_without_zero_fill():
    too_short = build_margin_analysis_card(
        _inputs(expected_holding_days=180),
        _snapshot(),
    )
    expensive = build_margin_analysis_card(
        _inputs(),
        _snapshot(margin_interest_rate=0.25),
    )

    assert "repayment_term_too_short" in too_short.hard_block_codes
    assert "financing_cost_extreme" in expensive.hard_block_codes


def test_short_fee_and_limited_inventory_remain_visible_as_warnings():
    card = build_margin_analysis_card(
        _inputs(
            mode="margin_short",
            market_regime="bearish",
            trend_return_20d=-0.05,
        ),
        _snapshot(
            short_availability=ShortAvailability.LIMITED,
            reverse_stock_borrow_fee=0.5,
        ),
    )

    assert card.analysis_status == MarginAnalysisStatus.WARNING
    assert "short_inventory_limited" in card.warning_codes
    assert "reverse_stock_borrow_fee_present" in card.warning_codes


def test_normalized_and_unknown_provider_restrictions_are_conservative():
    blocked = build_margin_analysis_card(
        _inputs(),
        _snapshot(restriction_codes=("new_margin_positions_suspended",)),
    )
    unknown = build_margin_analysis_card(
        _inputs(),
        _snapshot(restriction_codes=("provider_private_code_7",)),
    )

    assert "restriction_new_margin_positions_suspended" in blocked.hard_block_codes
    assert "unmapped_provider_restriction" in unknown.warning_codes
    assert unknown.human_review_required


@pytest.mark.parametrize("profile", ["leveraged", "inverse"])
def test_leveraged_and_inverse_etf_raise_risk_by_at_least_one_level(profile):
    card = build_margin_analysis_card(
        _inputs(
            etf_leverage_profile=profile,
            internal_leverage_multiple=None,
        ),
        _snapshot(),
    )

    assert card.analysis_status == MarginAnalysisStatus.WARNING
    assert card.risk_level == MarginRiskLevel.HIGH
    assert "etf_credit_leverage_overlap" in card.warning_codes
    assert "leveraged_etf_forced_liquidation_risk" in card.warning_codes
    assert "etf_internal_leverage_unknown" in card.warning_codes
    assert card.human_review_required


def test_leveraged_etf_with_another_warning_becomes_very_high_risk():
    card = build_margin_analysis_card(
        _inputs(
            etf_leverage_profile="leveraged",
            internal_leverage_multiple=2.0,
            days_to_event=4,
        ),
        _snapshot(),
    )

    assert card.risk_level == MarginRiskLevel.VERY_HIGH


def test_analysis_input_rejects_future_data_and_non_margin_modes():
    with pytest.raises(ValidationError, match="data_as_of cannot be after"):
        _inputs(data_as_of=NOW + timedelta(minutes=1))
    with pytest.raises(ValidationError, match="only margin_long or margin_short"):
        _inputs(mode=TradeMode.CASH)
    with pytest.raises(ValidationError, match="timezone-aware"):
        _inputs(as_of=datetime(2026, 8, 27))


def test_analysis_rejects_snapshot_identity_mismatch():
    with pytest.raises(ValueError, match="symbol"):
        build_margin_analysis_card(_inputs(), _snapshot(symbol="9999"))


def test_policy_is_ordered_and_versioned():
    with pytest.raises(ValidationError, match="extreme_atr_pct"):
        MarginRiskPolicy(high_atr_pct=0.1, extreme_atr_pct=0.1)

    card = build_margin_analysis_card(
        _inputs(),
        _snapshot(),
        policy=MarginRiskPolicy(version="test-risk-policy-v2"),
    )

    assert card.risk_rule_version == "test-risk-policy-v2"


def test_input_hash_is_stable_and_changes_with_point_in_time_input():
    first = build_margin_analysis_card(_inputs(), _snapshot())
    retry = build_margin_analysis_card(_inputs(), _snapshot())
    changed = build_margin_analysis_card(
        _inputs(trend_return_20d=0.09),
        _snapshot(),
    )

    assert first.input_hash == retry.input_hash
    assert first.input_hash != changed.input_hash


def test_us_liquidity_floor_uses_usd_and_market_specific_short_cost():
    inputs = _inputs(
        symbol="SPY",
        market="us",
        currency="USD",
        mode="margin_short",
        market_regime="bearish",
        trend_return_20d=-0.05,
        average_traded_value_20d=1_000_000,
        etf_leverage_profile="standard",
    )
    snapshot = _snapshot(
        symbol="SPY",
        market="us",
        exchange="NYSE",
        currency="USD",
        credit_types=("not_applicable",),
        is_lending_issue=None,
        borrow_cost=0.02,
    )

    card = build_margin_analysis_card(inputs, snapshot)

    assert card.analysis_status == MarginAnalysisStatus.CANDIDATE
    assert "liquidity_floor_passed" in card.supporting_factors


def test_stock_input_cannot_claim_an_etf_leverage_profile():
    with pytest.raises(ValidationError, match="stock inputs cannot"):
        _inputs(
            asset_type=MarginAssetType.STOCK,
            etf_leverage_profile="leveraged",
        )
