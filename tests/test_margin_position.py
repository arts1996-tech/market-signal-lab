from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.analysis.margin_risk import build_margin_analysis_card
from app.analysis.trade_modes import TradeMode
from app.backtest.audit import stable_payload_hash
from app.backtest.margin_position import (
    MarginEntryPlan,
    MarginExecutionPolicy,
    MarginOhlcBar,
    MarginPositionTerms,
    MarginTermsBasis,
    advance_margin_account_session,
    margin_account_summary,
    margin_position_terms_from_snapshot,
    new_margin_account,
    open_margin_position,
    size_margin_position,
)
from app.providers.margin import MarginMarket
from tests.test_margin_risk import _inputs
from tests.test_trade_modes import NOW, _snapshot


ENTRY_AT = NOW + timedelta(days=1)


def _terms(mode=TradeMode.MARGIN_LONG, **changes) -> MarginPositionTerms:
    values = {
        "provider_record_id": "record-1",
        "input_hash": "0" * 64,
        "asset_id": "asset-1",
        "symbol": "1306",
        "source": "synthetic_test",
        "source_version": "test-v1",
        "terms_basis": MarginTermsBasis.VERSIONED_RESEARCH_PROXY,
        "market": MarginMarket.JP,
        "currency": "JPY",
        "mode": mode,
        "initial_margin_rate": 0.30,
        "maintenance_margin_rate": 0.25,
        "minimum_margin_amount": 300_000,
        "margin_interest_rate": 0.028 if mode == TradeMode.MARGIN_LONG else None,
        "stock_lending_fee": 0.011 if mode == TradeMode.MARGIN_SHORT else None,
        "borrow_cost": None,
        "reverse_stock_borrow_fee_per_share_day": (
            0.10 if mode == TradeMode.MARGIN_SHORT else None
        ),
        "repayment_term_days": 180,
        "forced_liquidation_rule_version": "test-rule-v1",
        "effective_from": NOW - timedelta(days=1),
        "effective_to": None,
        "available_at": NOW - timedelta(hours=2),
        "fetched_at": NOW - timedelta(hours=1),
    }
    values.update(changes)
    if "input_hash" not in changes:
        values["input_hash"] = stable_payload_hash(
            {key: value for key, value in values.items() if key != "input_hash"}
        )
    return MarginPositionTerms(**values)


def _card(mode=TradeMode.MARGIN_LONG, **input_changes):
    defaults = {}
    if mode == TradeMode.MARGIN_SHORT:
        defaults = {
            "mode": mode,
            "market_regime": "bearish",
            "trend_return_20d": -0.08,
            "support_distance_pct": None,
        }
    defaults.update(input_changes)
    return build_margin_analysis_card(_inputs(**defaults), _snapshot())


def _plan(mode=TradeMode.MARGIN_LONG, **changes) -> MarginEntryPlan:
    values = {
        "asset_id": "asset-1",
        "symbol": "1306",
        "mode": mode,
        "decision_at": NOW,
        "entry_at": ENTRY_AT,
        "stop_price": 90 if mode == TradeMode.MARGIN_LONG else 110,
        "take_profit_price": 120 if mode == TradeMode.MARGIN_LONG else 80,
        "expected_holding_days": 20,
        "sector": "broad_market",
        "correlation_group": "broad_market_beta",
    }
    values.update(changes)
    return MarginEntryPlan(**values)


def _bar(at=ENTRY_AT, **changes) -> MarginOhlcBar:
    values = {
        "symbol": "1306",
        "price_time": at,
        "open": 100,
        "high": 103,
        "low": 97,
        "close": 101,
        "volume": 1_000_000,
    }
    values.update(changes)
    return MarginOhlcBar(**values)


def _open(
    *,
    mode=TradeMode.MARGIN_LONG,
    account=None,
    plan=None,
    bar=None,
    terms=None,
    card=None,
    policy=None,
    previous_volume=1_000_000,
):
    return open_margin_position(
        account or new_margin_account(account_name="short-term"),
        plan or _plan(mode),
        bar or _bar(),
        previous_volume=previous_volume,
        terms=terms or _terms(mode),
        analysis_card=card or _card(mode),
        policy=policy,
    )


def test_new_margin_account_starts_with_independent_2_5m_research_balance():
    account = new_margin_account(account_name="short-term")

    assert account.initial_cash == 2_500_000
    assert account.available_cash == 2_500_000
    assert account.research_only
    assert margin_account_summary(account) == {
        "equity": 2_500_000,
        "gross_notional": 0,
        "margin_notional": 0,
        "gross_leverage": 0.0,
        "margin_equity": 0,
        "maintenance_required": 0,
        "maintenance_ratio": None,
    }


def test_sizing_uses_the_smallest_risk_margin_liquidity_concentration_and_leverage_cap():
    account = new_margin_account(account_name="short-term")
    plan = _plan(requested_quantity=300)
    policy = MarginExecutionPolicy(lot_size=100)

    sizing = size_margin_position(
        account,
        plan,
        entry_price=100,
        entry_fx_rate=1,
        previous_volume=1_000_000,
        terms=_terms(),
        policy=policy,
    )

    assert sizing.quantity == 300
    assert sizing.binding_limit == "requested"
    assert all(
        capacity >= sizing.quantity
        for capacity in (
            sizing.risk_limited_quantity,
            sizing.margin_limited_quantity,
            sizing.liquidity_limited_quantity,
            sizing.position_limited_quantity,
            sizing.sector_limited_quantity,
            sizing.correlation_limited_quantity,
            sizing.leverage_limited_quantity,
        )
    )


def test_correlation_group_is_an_independent_position_size_cap():
    policy = MarginExecutionPolicy(maximum_correlation_notional_rate=0.05)
    first = _open(
        plan=_plan(requested_quantity=1_000),
        terms=_terms(minimum_margin_amount=0),
        policy=policy,
    )

    sizing = size_margin_position(
        first.account,
        _plan(sector="different-sector", requested_quantity=1_000),
        entry_price=100,
        entry_fx_rate=1,
        previous_volume=1_000_000,
        terms=_terms(minimum_margin_amount=0),
        policy=policy,
    )

    assert sizing.binding_limit == "correlation"
    assert sizing.quantity < 1_000


def test_missing_previous_volume_blocks_margin_sizing_instead_of_guessing_liquidity():
    result = _open(previous_volume=None)

    assert not result.accepted
    assert result.rejection_codes == ("previous_volume_missing",)
    assert result.sizing.quantity == 0


def test_entry_locks_margin_not_full_notional_and_never_sends_real_order():
    result = _open(
        plan=_plan(requested_quantity=1_000),
        terms=_terms(minimum_margin_amount=0),
    )

    assert result.accepted
    position = result.account.positions[0]
    assert position.quantity == 1_000
    assert position.margin_reserved == pytest.approx(position.entry_notional * 0.30)
    assert position.entry_notional > position.margin_reserved
    assert result.account.available_cash == pytest.approx(
        2_500_000 - position.margin_reserved - position.entry_fee
    )
    assert result.account.events[-1]["details"]["real_order_sent"] is False
    assert result.account.events[-1]["details"]["analysis_input_hash"]


def test_volume_cap_is_recorded_as_a_virtual_partial_fill():
    result = _open(
        plan=_plan(requested_quantity=10_000),
        previous_volume=5_000,
    )

    assert result.accepted
    assert result.account.positions[0].quantity == 500
    assert result.account.events[-1]["details"]["requested_quantity"] == 10_000
    assert result.account.events[-1]["details"]["partial_fill"] is True


def test_identical_frozen_entry_produces_a_deterministic_position_id():
    first = _open()
    retry = _open()

    assert first.position_id == retry.position_id
    assert first.account.events[-1]["event_id"] == retry.account.events[-1]["event_id"]


def test_terms_and_analysis_must_match_identity_provider_and_decision_time():
    with pytest.raises(ValueError, match="identity"):
        _open(terms=_terms(symbol="9999"))
    with pytest.raises(ValueError, match="provider record"):
        _open(terms=_terms(provider_record_id="different-record"))
    with pytest.raises(ValueError, match="as_of"):
        _open(plan=_plan(decision_at=NOW - timedelta(minutes=1)))


def test_future_terms_and_blocked_analysis_do_not_open_positions():
    future_terms = _open(
        terms=_terms(
            available_at=NOW + timedelta(minutes=1),
            fetched_at=NOW + timedelta(minutes=2),
        )
    )
    blocked_card = _card(risk_reward_ratio=1.0)
    blocked = _open(card=blocked_card)

    assert future_terms.rejection_codes == ("margin_terms_not_known_at_decision",)
    assert blocked.rejection_codes == ("margin_analysis_not_eligible",)
    assert not future_terms.account.positions
    assert not blocked.account.positions


def test_warning_card_requires_explicit_human_review_for_virtual_entry():
    warning = _card(atr_pct=0.06)

    rejected = _open(card=warning)
    accepted = _open(
        card=warning,
        plan=_plan(human_review_approved=True),
    )

    assert rejected.rejection_codes == ("margin_analysis_review_not_approved",)
    assert accepted.accepted


def test_long_stop_gap_uses_open_and_includes_calendar_financing_cost():
    opened = _open(plan=_plan(requested_quantity=1_000))
    session_at = ENTRY_AT + timedelta(days=3)

    result = advance_margin_account_session(
        opened.account,
        (
            _bar(
                at=session_at,
                open=80,
                high=82,
                low=75,
                close=78,
            ),
        ),
    )

    event = result.session_events[-1]
    assert not result.account.positions
    assert event["details"]["reason"] == "stop_loss_gap"
    assert event["details"]["execution_price"] < 80
    assert event["details"]["financing_cost"] > 0
    assert event["details"]["realized_pnl"] < 0
    assert event["details"]["real_order_sent"] is False


def test_short_take_profit_and_short_squeeze_have_opposite_pnl_direction():
    profitable = _open(
        mode=TradeMode.MARGIN_SHORT,
        plan=_plan(TradeMode.MARGIN_SHORT, requested_quantity=1_000),
    )
    profit_result = advance_margin_account_session(
        profitable.account,
        (_bar(at=ENTRY_AT + timedelta(days=1), open=75, high=77, low=70, close=72),),
    )

    losing = _open(
        mode=TradeMode.MARGIN_SHORT,
        plan=_plan(TradeMode.MARGIN_SHORT, requested_quantity=1_000),
    )
    loss_result = advance_margin_account_session(
        losing.account,
        (_bar(at=ENTRY_AT + timedelta(days=1), open=125, high=130, low=122, close=128),),
    )

    assert profit_result.session_events[-1]["details"]["realized_pnl"] > 0
    assert loss_result.session_events[-1]["details"]["realized_pnl"] < 0
    assert loss_result.session_events[-1]["details"]["reason"] == "stop_loss_gap"


@pytest.mark.parametrize("mode", [TradeMode.MARGIN_LONG, TradeMode.MARGIN_SHORT])
def test_same_session_stop_and_take_profit_uses_conservative_stop_first(mode):
    opened = _open(mode=mode, plan=_plan(mode, requested_quantity=1_000))
    result = advance_margin_account_session(
        opened.account,
        (
            _bar(
                at=ENTRY_AT + timedelta(days=1),
                open=100,
                high=125,
                low=75,
                close=100,
            ),
        ),
    )

    assert result.session_events[-1]["details"]["reason"] == "stop_loss"
    assert result.session_events[-1]["details"]["realized_pnl"] < 0


def test_regular_stop_blocked_by_limit_down_is_deferred_to_next_tradable_open():
    opened = _open(plan=_plan(requested_quantity=1_000))
    blocked_at = ENTRY_AT + timedelta(days=1)
    deferred = advance_margin_account_session(
        opened.account,
        (
            _bar(
                at=blocked_at,
                open=85,
                high=86,
                low=80,
                close=82,
                limit_down=True,
            ),
        ),
    )

    assert deferred.exit_pending
    assert deferred.session_events[-1]["event_type"] == "exit_deferred"
    assert deferred.session_events[-1]["details"]["reason"] == "stop_loss_gap"
    assert deferred.account.positions[0].exit_pending_reason == "stop_loss_gap"

    next_at = blocked_at + timedelta(days=1)
    closed = advance_margin_account_session(
        deferred.account,
        (_bar(at=next_at, open=75, high=78, low=70, close=72),),
    )

    assert not closed.account.positions
    assert closed.session_events[-1]["details"]["reason"] == (
        "deferred_exit:stop_loss_gap"
    )
    assert closed.session_events[-1]["details"]["execution_price"] < 75


def test_japanese_short_financing_includes_lending_and_reverse_borrow_fee():
    opened = _open(
        mode=TradeMode.MARGIN_SHORT,
        plan=_plan(
            TradeMode.MARGIN_SHORT,
            requested_quantity=1_000,
            stop_price=200,
            take_profit_price=50,
        ),
    )
    result = advance_margin_account_session(
        opened.account,
        (_bar(at=ENTRY_AT + timedelta(days=2), open=100, high=105, low=95, close=100),),
    )

    position = result.account.positions[0]
    annual_cost = position.entry_notional * 0.011 * 2 / 365
    reverse_cost = 0.10 * position.quantity * 2
    assert position.accrued_financing_cost == pytest.approx(
        annual_cost + reverse_cost
    )
    assert result.session_events[-1]["details"]["days"] == 2


def test_maintenance_breach_schedules_next_open_forced_liquidation():
    high_risk_policy = MarginExecutionPolicy(
        maximum_risk_per_trade_rate=0.50,
        maximum_total_open_risk_rate=0.50,
    )
    opened = _open(
        plan=_plan(stop_price=1, take_profit_price=200),
        policy=high_risk_policy,
    )
    breach_at = ENTRY_AT + timedelta(days=1)
    breached = advance_margin_account_session(
        opened.account,
        (_bar(at=breach_at, open=100, high=101, low=20, close=20),),
        policy=high_risk_policy,
    )

    assert breached.forced_liquidation_pending
    assert breached.margin_equity <= breached.maintenance_required
    assert breached.session_events[-1]["event_type"] == "forced_liquidation_scheduled"

    next_at = breach_at + timedelta(days=1)
    liquidated = advance_margin_account_session(
        breached.account,
        (_bar(at=next_at, open=18, high=20, low=15, close=16),),
        policy=high_risk_policy,
    )
    exit_event = liquidated.session_events[-1]
    assert not liquidated.account.positions
    assert exit_event["details"]["reason"].startswith("forced_liquidation:")
    assert exit_event["details"]["execution_price"] < 18


def test_short_forced_buyback_can_be_deferred_at_limit_up_then_closes_later():
    high_risk_policy = MarginExecutionPolicy(
        maximum_risk_per_trade_rate=0.50,
        maximum_total_open_risk_rate=0.50,
    )
    opened = _open(
        mode=TradeMode.MARGIN_SHORT,
        plan=_plan(
            TradeMode.MARGIN_SHORT,
            stop_price=500,
            take_profit_price=50,
        ),
        policy=high_risk_policy,
    )
    breach_at = ENTRY_AT + timedelta(days=1)
    breached = advance_margin_account_session(
        opened.account,
        (
            _bar(
                at=breach_at,
                open=100,
                high=300,
                low=99,
                close=300,
                limit_up=True,
            ),
        ),
        policy=high_risk_policy,
    )
    assert breached.forced_liquidation_pending

    deferred_at = breach_at + timedelta(days=1)
    deferred = advance_margin_account_session(
        breached.account,
        (
            _bar(
                at=deferred_at,
                open=330,
                high=350,
                low=320,
                close=350,
                limit_up=True,
            ),
        ),
        policy=high_risk_policy,
    )
    assert deferred.session_events[-1]["event_type"] == "forced_liquidation_deferred"
    assert deferred.account.positions

    close_at = deferred_at + timedelta(days=1)
    closed = advance_margin_account_session(
        deferred.account,
        (_bar(at=close_at, open=360, high=370, low=340, close=350),),
        policy=high_risk_policy,
    )
    assert not closed.account.positions
    assert closed.session_events[-1]["details"]["execution_price"] > 360
    assert closed.session_events[-1]["details"]["realized_pnl"] < 0


def test_repayment_deadline_forces_next_available_session_open():
    opened = _open(
        plan=_plan(stop_price=50, take_profit_price=150),
        terms=_terms(repayment_term_days=2),
    )
    deadline = ENTRY_AT + timedelta(days=2)

    result = advance_margin_account_session(
        opened.account,
        (_bar(at=deadline, open=105, high=110, low=100, close=108),),
    )

    assert not result.account.positions
    assert result.session_events[-1]["details"]["reason"] == (
        "forced_liquidation:repayment_deadline_reached"
    )


def test_short_terms_require_explicit_costs_instead_of_treating_unknown_as_zero():
    with pytest.raises(ValidationError, match="stock_lending_fee"):
        _terms(TradeMode.MARGIN_SHORT, stock_lending_fee=None)
    with pytest.raises(ValidationError, match="explicit reverse borrow fee"):
        _terms(
            TradeMode.MARGIN_SHORT,
            reverse_stock_borrow_fee_per_share_day=None,
        )
    with pytest.raises(ValidationError, match="initial margin rate"):
        _terms(initial_margin_rate=0.20, maintenance_margin_rate=0.25)
    with pytest.raises(ValidationError, match="borrow_cost"):
        _terms(
            TradeMode.MARGIN_SHORT,
            market=MarginMarket.US,
            currency="USD",
            symbol="SPY",
            borrow_cost=None,
            reverse_stock_borrow_fee_per_share_day=None,
        )


def test_provider_snapshot_is_frozen_into_hashed_execution_terms():
    snapshot = _snapshot()
    terms = margin_position_terms_from_snapshot(
        snapshot,
        TradeMode.MARGIN_LONG,
        terms_basis=MarginTermsBasis.VERIFIED_PROVIDER,
    )

    assert terms.asset_id == snapshot.asset_id
    assert terms.provider_record_id == snapshot.provider_record_id
    assert terms.minimum_margin_amount == snapshot.minimum_margin_amount
    assert terms.input_hash == margin_position_terms_from_snapshot(
        snapshot,
        TradeMode.MARGIN_LONG,
        terms_basis=MarginTermsBasis.VERIFIED_PROVIDER,
    ).input_hash

    with pytest.raises(ValueError, match="minimum_margin_amount"):
        margin_position_terms_from_snapshot(
            _snapshot(minimum_margin_amount=None),
            TradeMode.MARGIN_LONG,
            terms_basis=MarginTermsBasis.VERIFIED_PROVIDER,
        )


def test_effective_terms_update_changes_financing_and_maintenance_without_retroactivity():
    policy = MarginExecutionPolicy(
        maximum_risk_per_trade_rate=0.50,
        maximum_total_open_risk_rate=0.50,
    )
    original_terms = _terms(minimum_margin_amount=0)
    opened = _open(
        plan=_plan(stop_price=1, take_profit_price=200, requested_quantity=10_000),
        terms=original_terms,
        policy=policy,
    )
    session_at = ENTRY_AT + timedelta(days=2)
    new_terms = _terms(
        provider_record_id="record-2",
        source_version="test-v2",
        minimum_margin_amount=0,
        initial_margin_rate=0.90,
        maintenance_margin_rate=0.80,
        margin_interest_rate=0.10,
        effective_from=ENTRY_AT + timedelta(days=1),
        available_at=ENTRY_AT + timedelta(days=1),
        fetched_at=ENTRY_AT + timedelta(days=1),
    )

    result = advance_margin_account_session(
        opened.account,
        (_bar(at=session_at, open=100, high=101, low=99, close=99),),
        policy=policy,
        terms_updates=(new_terms,),
    )

    position = result.account.positions[0]
    old_day = position.entry_notional * 0.028 / 365
    new_day = position.entry_notional * 0.10 / 365
    assert position.accrued_financing_cost == pytest.approx(old_day + new_day)
    assert position.margin_terms.input_hash == new_terms.input_hash
    assert result.forced_liquidation_pending
    assert result.session_events[0]["details"]["margin_terms_input_hash"] == (
        new_terms.input_hash
    )


def test_new_entry_is_blocked_while_forced_liquidation_is_pending():
    policy = MarginExecutionPolicy(
        maximum_risk_per_trade_rate=0.50,
        maximum_total_open_risk_rate=0.50,
    )
    opened = _open(
        plan=_plan(stop_price=1, take_profit_price=200),
        policy=policy,
    )
    breach_at = ENTRY_AT + timedelta(days=1)
    breached = advance_margin_account_session(
        opened.account,
        (_bar(at=breach_at, open=100, high=101, low=20, close=20),),
        policy=policy,
    )
    next_at = breach_at + timedelta(days=1)
    next_card = _card(
        as_of=breach_at,
        data_as_of=breach_at - timedelta(minutes=30),
    )

    result = _open(
        account=breached.account,
        plan=_plan(decision_at=breach_at, entry_at=next_at),
        bar=_bar(at=next_at),
        card=next_card,
        policy=policy,
    )

    assert not result.accepted
    assert result.rejection_codes == ("forced_liquidation_pending",)


def test_session_rejects_missing_duplicate_or_non_forward_bars():
    opened = _open()
    future = ENTRY_AT + timedelta(days=1)

    with pytest.raises(ValueError, match="missing OHLC"):
        advance_margin_account_session(
            opened.account,
            (_bar(at=future, symbol="9999"),),
        )
    with pytest.raises(ValueError, match="duplicate"):
        advance_margin_account_session(
            opened.account,
            (_bar(at=future), _bar(at=future)),
        )
    with pytest.raises(ValueError, match="forward"):
        advance_margin_account_session(opened.account, (_bar(at=ENTRY_AT),))
