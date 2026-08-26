from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.analysis.trade_modes import (
    EligibilityStatus,
    TradeMode,
    assess_all_trade_modes,
    assess_trade_mode_data,
)
from app.providers.margin import (
    CreditType,
    MarginAssetType,
    MarginDataQuality,
    MarginMarket,
    MarginSnapshotQuery,
    MarginTradingProvider,
    MarginTradingSnapshot,
    ShortAvailability,
)


NOW = datetime(2026, 8, 27, 0, 0, tzinfo=timezone.utc)


def _snapshot(**changes) -> MarginTradingSnapshot:
    values = {
        "provider_record_id": "record-1",
        "asset_id": "asset-1",
        "symbol": "1306",
        "market": MarginMarket.JP,
        "exchange": "JPX",
        "asset_type": MarginAssetType.ETF,
        "broker_scope": "market_public",
        "source": "synthetic_test",
        "source_version": "test-v1",
        "currency": "JPY",
        "margin_long_eligible": True,
        "margin_short_eligible": True,
        "credit_types": (CreditType.STANDARDIZED,),
        "is_lending_issue": True,
        "short_availability": ShortAvailability.AVAILABLE,
        "margin_interest_rate": 0.028,
        "stock_lending_fee": 0.011,
        "borrow_cost": None,
        "reverse_stock_borrow_fee": 0.0,
        "initial_margin_rate": 0.30,
        "maintenance_margin_rate": 0.25,
        "minimum_margin_amount": 300_000,
        "repayment_term_days": 180,
        "forced_liquidation_rule_version": "test-rule-v1",
        "effective_from": NOW - timedelta(days=1),
        "effective_to": None,
        "available_at": NOW - timedelta(hours=2),
        "fetched_at": NOW - timedelta(hours=1),
        "data_quality": MarginDataQuality.VERIFIED,
    }
    values.update(changes)
    return MarginTradingSnapshot(**values)


def test_trade_mode_vocabulary_is_closed_and_complete():
    assert {mode.value for mode in TradeMode} == {
        "cash",
        "margin_long",
        "margin_short",
        "auto_select",
    }
    with pytest.raises(ValueError):
        assess_trade_mode_data("broker_order", None, as_of=NOW)


def test_cash_does_not_require_margin_data_but_missing_margin_data_never_falls_back():
    results = assess_all_trade_modes(None, as_of=NOW)

    assert results["cash"].status == EligibilityStatus.ELIGIBLE
    assert results["margin_long"].status == EligibilityStatus.INSUFFICIENT_DATA
    assert results["margin_short"].status == EligibilityStatus.INSUFFICIENT_DATA
    assert results["auto_select"].status == EligibilityStatus.INSUFFICIENT_DATA
    assert results["margin_long"].reason_codes == ("margin_snapshot_missing",)
    assert results["auto_select"].reason_codes == (
        "auto_select_strategy_comparison_not_implemented",
    )


def test_verified_snapshot_can_pass_long_and_short_data_boundaries():
    snapshot = _snapshot()

    long_result = assess_trade_mode_data(TradeMode.MARGIN_LONG, snapshot, as_of=NOW)
    short_result = assess_trade_mode_data(TradeMode.MARGIN_SHORT, snapshot, as_of=NOW)

    assert long_result.status == EligibilityStatus.ELIGIBLE
    assert short_result.status == EligibilityStatus.ELIGIBLE
    assert not long_result.human_review_required
    assert not short_result.human_review_required


def test_unknown_or_stale_margin_data_is_not_treated_as_eligible_or_zero_cost():
    snapshot = _snapshot(
        margin_long_eligible=None,
        margin_interest_rate=None,
        data_quality=MarginDataQuality.STALE,
        available_at=NOW - timedelta(days=30),
        fetched_at=NOW - timedelta(days=29),
    )

    result = assess_trade_mode_data(TradeMode.MARGIN_LONG, snapshot, as_of=NOW)

    assert result.status == EligibilityStatus.INSUFFICIENT_DATA
    assert "margin_long_eligibility_unknown" in result.reason_codes
    assert "missing_margin_interest_rate" in result.reason_codes
    assert "margin_data_stale" in result.reason_codes
    assert "margin_data_quality_stale" in result.reason_codes


def test_future_fetched_or_synthetic_data_cannot_pass_the_decision_boundary():
    result = assess_trade_mode_data(
        TradeMode.MARGIN_LONG,
        _snapshot(
            fetched_at=NOW + timedelta(minutes=1),
            data_quality=MarginDataQuality.SYNTHETIC_RESEARCH,
        ),
        as_of=NOW,
    )

    assert result.status == EligibilityStatus.INSUFFICIENT_DATA
    assert "margin_data_fetched_after_decision" in result.reason_codes
    assert "margin_data_quality_synthetic_research" in result.reason_codes


def test_explicit_ineligibility_is_distinct_from_missing_data():
    result = assess_trade_mode_data(
        TradeMode.MARGIN_SHORT,
        _snapshot(margin_short_eligible=False),
        as_of=NOW,
    )

    assert result.status == EligibilityStatus.NOT_ELIGIBLE
    assert result.reason_codes == ("margin_short_explicitly_not_eligible",)


def test_future_explicit_ineligibility_is_not_used_before_it_was_fetched():
    result = assess_trade_mode_data(
        TradeMode.MARGIN_SHORT,
        _snapshot(
            margin_short_eligible=False,
            fetched_at=NOW + timedelta(minutes=1),
        ),
        as_of=NOW,
    )

    assert result.status == EligibilityStatus.INSUFFICIENT_DATA
    assert result.reason_codes == ("margin_data_fetched_after_decision",)


def test_us_short_boundary_uses_market_specific_borrow_cost_not_japanese_types():
    snapshot = _snapshot(
        symbol="SPY",
        market=MarginMarket.US,
        exchange="NYSE",
        currency="USD",
        credit_types=(CreditType.NOT_APPLICABLE,),
        is_lending_issue=None,
        borrow_cost=0.02,
    )

    result = assess_trade_mode_data(TradeMode.MARGIN_SHORT, snapshot, as_of=NOW)

    assert result.status == EligibilityStatus.ELIGIBLE


def test_us_snapshot_rejects_japanese_credit_classifications():
    with pytest.raises(ValidationError, match="Japanese credit types"):
        _snapshot(
            symbol="SPY",
            market=MarginMarket.US,
            exchange="NYSE",
            currency="USD",
            credit_types=(CreditType.STANDARDIZED,),
            is_lending_issue=None,
        )


def test_margin_snapshot_rejects_naive_or_inconsistent_timestamps():
    with pytest.raises(ValidationError, match="timezone-aware"):
        _snapshot(available_at=datetime(2026, 8, 26, 0, 0))
    with pytest.raises(ValidationError, match="fetched_at cannot be before"):
        _snapshot(fetched_at=NOW - timedelta(days=2))

    with pytest.raises(ValidationError, match="as_of must be timezone-aware"):
        MarginSnapshotQuery(
            asset_id="asset-1",
            symbol="1306",
            market="jp",
            exchange="JPX",
            asset_type="etf",
            broker_scope="market_public",
            as_of=datetime(2026, 8, 27),
        )


def test_provider_protocol_has_no_broker_order_surface():
    class FakeProvider:
        name = "fake"
        broker_scope = "market_public"

        def fetch_margin_snapshots(self, queries):
            return tuple(_snapshot(asset_id=query.asset_id) for query in queries)

        def health_check(self):
            return {"ok": True}

    query = MarginSnapshotQuery(
        asset_id="asset-1",
        symbol="1306",
        market="jp",
        exchange="JPX",
        asset_type="etf",
        broker_scope="market_public",
        as_of=NOW,
    )
    provider = FakeProvider()

    assert isinstance(provider, MarginTradingProvider)
    assert provider.fetch_margin_snapshots((query,))[0].asset_id == "asset-1"
    assert not hasattr(provider, "place_order")
    assert not hasattr(provider, "authenticate_broker")
