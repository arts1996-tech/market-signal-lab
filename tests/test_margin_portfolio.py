from datetime import timedelta

import pandas as pd
import pytest

from app.analysis.trade_modes import TradeMode
from app.analysis.mode_selection import (
    CashExecutionCard,
    ModeCandidateRisk,
    ModeSelectionCandidate,
    select_trade_mode,
)
from app.analysis.trade_modes import EligibilityStatus
from app.backtest.audit import stable_payload_hash
from app.backtest.margin_portfolio import (
    MARGIN_PORTFOLIO_EXECUTION_VERSION,
    MarginBacktestCandidate,
    PositionCashflowEvent,
    simulate_auto_select_portfolio,
    simulate_margin_mode_portfolio,
)
from app.backtest.margin_position import (
    MarginExecutionPolicy,
    cash_position_terms,
)
from app.providers.margin import MarginMarket
from tests.test_trade_modes import NOW
from tests.test_margin_position import ENTRY_AT, _bar, _card, _plan, _terms


def _candidate(
    mode=TradeMode.MARGIN_LONG,
    *,
    candidate_id="candidate-1",
    decision_id=None,
    plan=None,
    entry_bar=None,
    terms=None,
    card=None,
):
    selected_plan = plan or _plan(mode, requested_quantity=1_000)
    selected_bar = entry_bar or _bar(at=selected_plan.entry_at)
    return MarginBacktestCandidate(
        candidate_id=candidate_id,
        decision_id=decision_id or candidate_id,
        plan=selected_plan,
        entry_bar=selected_bar,
        previous_volume=1_000_000,
        terms=terms or _terms(mode),
        analysis_card=card or _card(mode),
    )


def _selection_candidate(mode, input_hash, *, score):
    return ModeSelectionCandidate(
        mode=mode,
        input_hash=input_hash,
        data_as_of=NOW,
        eligibility_status=EligibilityStatus.ELIGIBLE,
        analysis_status="candidate",
        pretrade_score=score,
        expected_risk_reward=2,
        risk_level=ModeCandidateRisk.MODERATE,
    )


def test_isolated_margin_long_and_short_series_keep_directional_results_separate():
    long_exit = _bar(
        at=ENTRY_AT + timedelta(days=1),
        open=105,
        high=125,
        low=104,
        close=120,
    )
    long_result = simulate_margin_mode_portfolio(
        (_candidate(),),
        (_bar(), long_exit),
        trade_mode=TradeMode.MARGIN_LONG,
        account_name="long-series",
    )

    short_plan = _plan(
        TradeMode.MARGIN_SHORT,
        requested_quantity=1_000,
    )
    short_exit = _bar(
        at=ENTRY_AT + timedelta(days=1),
        open=95,
        high=96,
        low=75,
        close=80,
    )
    short_result = simulate_margin_mode_portfolio(
        (_candidate(TradeMode.MARGIN_SHORT, plan=short_plan),),
        (_bar(), short_exit),
        trade_mode=TradeMode.MARGIN_SHORT,
        account_name="short-series",
    )

    assert long_result["trade_mode"] == "margin_long"
    assert short_result["trade_mode"] == "margin_short"
    assert long_result["realized_pnl"] > 0
    assert short_result["realized_pnl"] > 0
    assert long_result["metrics"]["closed_trades"] == 1
    assert short_result["metrics"]["closed_trades"] == 1
    assert set(long_result["events"]["details"].map(lambda item: item.get("mode")) .dropna()) == {
        "margin_long"
    }
    assert set(short_result["events"]["details"].map(lambda item: item.get("mode")) .dropna()) == {
        "margin_short"
    }


def test_same_open_exit_proceeds_and_position_slot_do_not_fund_new_entry():
    second_at = ENTRY_AT + timedelta(days=1)
    first = _candidate()
    second = _candidate(
        candidate_id="candidate-2",
        plan=_plan(entry_at=second_at, requested_quantity=1_000),
        entry_bar=_bar(
            at=second_at,
            open=120,
            high=125,
            low=118,
            close=122,
        ),
    )

    result = simulate_margin_mode_portfolio(
        (first, second),
        (
            _bar(),
            _bar(
                at=second_at,
                open=120,
                high=125,
                low=118,
                close=122,
            ),
        ),
        trade_mode=TradeMode.MARGIN_LONG,
    )

    same_open = result["events"][
        result["events"]["event_at"] == pd.Timestamp(second_at)
    ]
    assert list(same_open["event_type"])[:2] == ["rejected", "exit"]
    rejected = result["rejected_entries"].iloc[0]
    assert rejected["details"]["reason_codes"] == ["symbol_already_held"]


def test_margin_runner_tracks_financing_fees_drawdown_and_benchmark_separately():
    dates = [ENTRY_AT + timedelta(days=offset) for offset in range(3)]
    bars = (
        _bar(at=dates[0]),
        _bar(at=dates[1], open=100, high=102, low=95, close=96),
        _bar(at=dates[2], open=80, high=82, low=75, close=78),
    )
    benchmark = pd.Series([100, 101, 99], index=pd.DatetimeIndex(dates))

    result = simulate_margin_mode_portfolio(
        (_candidate(),),
        bars,
        trade_mode=TradeMode.MARGIN_LONG,
        benchmark=benchmark,
    )

    metrics = result["metrics"]
    assert metrics["closed_trades"] == 1
    assert metrics["gross_fees"] > 0
    assert metrics["financing_cost"] > 0
    assert metrics["maximum_drawdown"] < 0
    assert metrics["benchmark_return"] == pytest.approx(-0.01)
    assert metrics["excess_return"] is not None


def test_maintenance_breach_and_next_open_liquidation_are_counted():
    policy = MarginExecutionPolicy(
        maximum_risk_per_trade_rate=0.50,
        maximum_total_open_risk_rate=0.50,
    )
    candidate = _candidate(
        plan=_plan(stop_price=1, take_profit_price=200),
    )
    breach_at = ENTRY_AT + timedelta(days=1)
    liquidate_at = breach_at + timedelta(days=1)

    result = simulate_margin_mode_portfolio(
        (candidate,),
        (
            _bar(),
            _bar(at=breach_at, open=100, high=101, low=20, close=20),
            _bar(at=liquidate_at, open=18, high=20, low=15, close=16),
        ),
        trade_mode=TradeMode.MARGIN_LONG,
        policy=policy,
    )

    assert result["metrics"]["forced_liquidations"] == 1
    assert result["realized_pnl"] < 0
    assert result["positions"].empty


def test_manifest_is_deterministic_and_never_represents_a_real_order():
    candidate = _candidate()
    bars = (
        _bar(),
        _bar(
            at=ENTRY_AT + timedelta(days=1),
            open=105,
            high=125,
            low=104,
            close=120,
        ),
    )

    first = simulate_margin_mode_portfolio(
        (candidate,), bars, trade_mode=TradeMode.MARGIN_LONG
    )
    retry = simulate_margin_mode_portfolio(
        (candidate,), tuple(reversed(bars)), trade_mode=TradeMode.MARGIN_LONG
    )

    assert first["manifest"]["run_id"] == retry["manifest"]["run_id"]
    assert first["manifest"]["execution_version"] == MARGIN_PORTFOLIO_EXECUTION_VERSION
    assert first["manifest"]["real_order_sent"] is False
    assert first["real_order_sent"] is False
    assert all(
        not details.get("real_order_sent", False)
        for details in first["events"]["details"]
    )
    assert (
        "corporate_action_coverage_unverified"
        in first["quality_warnings"]
    )


@pytest.mark.parametrize(
    ("mode", "expected_sign"),
    [
        (TradeMode.MARGIN_LONG, 1),
        (TradeMode.MARGIN_SHORT, -1),
    ],
)
def test_dividend_entitlement_is_income_for_long_and_cost_for_short(mode, expected_sign):
    candidate = _candidate(
        mode,
        plan=_plan(
            mode,
            stop_price=50 if mode == TradeMode.MARGIN_LONG else 200,
            take_profit_price=200 if mode == TradeMode.MARGIN_LONG else 50,
            requested_quantity=1_000,
        ),
    )
    entitlement_at = ENTRY_AT + timedelta(days=1)
    payment_at = ENTRY_AT + timedelta(days=2)
    cashflow = PositionCashflowEvent(
        cashflow_id=f"dividend-{mode.value}",
        candidate_id=candidate.candidate_id,
        symbol="1306",
        mode=mode,
        entitlement_at=entitlement_at,
        payment_at=payment_at,
        amount_per_share=1,
        fx_rate_to_account=1,
        cashflow_kind=(
            "short_dividend_equivalent"
            if mode == TradeMode.MARGIN_SHORT
            else "cash_dividend"
        ),
        available_at=payment_at,
        input_hash=stable_payload_hash({"cashflow": mode.value}),
    )
    coverage_hash = stable_payload_hash({"coverage": "complete"})

    result = simulate_margin_mode_portfolio(
        (candidate,),
        (
            _bar(),
            _bar(at=entitlement_at),
            _bar(at=payment_at),
        ),
        trade_mode=mode,
        position_cashflows=(cashflow,),
        corporate_action_coverage_status="verified_supported_only",
        corporate_action_coverage_hash=coverage_hash,
    )

    entry_quantity = result["events"].query("event_type == 'entry'").iloc[0][
        "details"
    ]["quantity"]
    expected_cashflow = expected_sign * entry_quantity
    assert result["metrics"]["position_cashflow_pnl"] == expected_cashflow
    if mode == TradeMode.MARGIN_SHORT:
        assert result["metrics"]["short_dividend_equivalent_cost"] == entry_quantity
    else:
        assert result["metrics"]["dividend_or_distribution_income"] == entry_quantity
    assert "corporate_action_coverage_unverified" not in result["quality_warnings"]
    event = result["events"].query("event_type == 'position_cashflow'").iloc[0]
    assert event["details"]["real_order_sent"] is False


def test_cashflow_is_not_granted_when_position_was_not_held_at_entitlement():
    candidate = _candidate()
    entitlement_at = ENTRY_AT
    payment_at = ENTRY_AT + timedelta(days=1)
    cashflow = PositionCashflowEvent(
        cashflow_id="same-day-entitlement",
        candidate_id=candidate.candidate_id,
        symbol="1306",
        mode=TradeMode.MARGIN_LONG,
        entitlement_at=entitlement_at,
        payment_at=payment_at,
        amount_per_share=10,
        fx_rate_to_account=1,
        cashflow_kind="cash_dividend",
        available_at=payment_at,
        input_hash=stable_payload_hash({"cashflow": "same-day"}),
    )

    result = simulate_margin_mode_portfolio(
        (candidate,),
        (_bar(), _bar(at=payment_at)),
        trade_mode=TradeMode.MARGIN_LONG,
        position_cashflows=(cashflow,),
    )

    assert result["metrics"]["position_cashflow_pnl"] == 0
    assert "position_cashflow" not in set(result["events"]["event_type"])


def test_verified_corporate_action_coverage_requires_a_version_hash():
    with pytest.raises(ValueError, match="coverage requires a hash"):
        simulate_margin_mode_portfolio(
            (_candidate(),),
            (_bar(),),
            trade_mode=TradeMode.MARGIN_LONG,
            corporate_action_coverage_status="verified_supported_only",
        )


def test_verified_corporate_action_coverage_rejects_non_hex_hash():
    with pytest.raises(ValueError, match="coverage requires a hash"):
        simulate_margin_mode_portfolio(
            (_candidate(),),
            (_bar(),),
            trade_mode=TradeMode.MARGIN_LONG,
            corporate_action_coverage_status="verified_supported_only",
            corporate_action_coverage_hash="z" * 64,
        )


@pytest.mark.parametrize(
    ("mode", "cashflow_kind"),
    [
        (TradeMode.MARGIN_LONG, "short_dividend_equivalent"),
        (TradeMode.MARGIN_SHORT, "cash_dividend"),
    ],
)
def test_position_cashflow_kind_must_match_position_direction(
    mode, cashflow_kind
):
    with pytest.raises(ValueError):
        PositionCashflowEvent(
            cashflow_id="invalid-cashflow",
            candidate_id="candidate-1",
            symbol="1306",
            mode=mode,
            entitlement_at=ENTRY_AT,
            payment_at=ENTRY_AT + timedelta(days=1),
            amount_per_share=1,
            fx_rate_to_account=1,
            cashflow_kind=cashflow_kind,
            available_at=ENTRY_AT + timedelta(days=1),
            input_hash=stable_payload_hash({"cashflow": "invalid"}),
        )


def test_auto_select_executes_cash_then_margin_short_in_one_frozen_account():
    cash_hash = stable_payload_hash({"decision": "cash"})
    cash_card = CashExecutionCard(
        asset_id="asset-1",
        symbol="1306",
        as_of=NOW,
        data_as_of=NOW,
        input_hash=cash_hash,
    )
    cash_plan = _plan(
        TradeMode.CASH,
        stop_price=90,
        take_profit_price=110,
        requested_quantity=1_000,
    )
    cash_candidate = _candidate(
        TradeMode.CASH,
        candidate_id="cash-execution",
        decision_id="decision-cash",
        plan=cash_plan,
        terms=cash_position_terms(
            asset_id="asset-1",
            symbol="1306",
            market=MarginMarket.JP,
            currency="JPY",
            as_of=NOW,
        ),
        card=cash_card,
    )
    long_hash = stable_payload_hash({"decision": "unused-long"})
    short_hash = stable_payload_hash({"decision": "unused-short"})
    cash_decision = select_trade_mode(
        decision_id="decision-cash",
        decision_at=NOW,
        candidates=(
            _selection_candidate(TradeMode.CASH, cash_hash, score=90),
            _selection_candidate(TradeMode.MARGIN_LONG, long_hash, score=70),
            _selection_candidate(TradeMode.MARGIN_SHORT, short_hash, score=65),
        ),
    )

    short_decision_at = ENTRY_AT + timedelta(days=1)
    short_entry_at = short_decision_at + timedelta(days=1)
    short_card = _card(
        TradeMode.MARGIN_SHORT,
        as_of=short_decision_at,
        data_as_of=short_decision_at - timedelta(minutes=30),
    )
    short_candidate = _candidate(
        TradeMode.MARGIN_SHORT,
        candidate_id="short-execution",
        decision_id="decision-short",
        plan=_plan(
            TradeMode.MARGIN_SHORT,
            decision_at=short_decision_at,
            entry_at=short_entry_at,
            requested_quantity=1_000,
        ),
        entry_bar=_bar(at=short_entry_at),
        card=short_card,
    )
    short_decision = select_trade_mode(
        decision_id="decision-short",
        decision_at=short_decision_at,
        candidates=(
            _selection_candidate(
                TradeMode.CASH,
                stable_payload_hash({"decision": "unused-cash-2"}),
                score=65,
            ),
            _selection_candidate(
                TradeMode.MARGIN_LONG,
                stable_payload_hash({"decision": "unused-long-2"}),
                score=70,
            ),
            ModeSelectionCandidate(
                mode=TradeMode.MARGIN_SHORT,
                input_hash=short_card.input_hash,
                data_as_of=short_decision_at - timedelta(minutes=30),
                eligibility_status=EligibilityStatus.ELIGIBLE,
                analysis_status="candidate",
                pretrade_score=90,
                expected_risk_reward=2,
                risk_level=ModeCandidateRisk.MODERATE,
            ),
        ),
    )
    cash_exit_at = ENTRY_AT + timedelta(days=1)
    short_exit_at = short_entry_at + timedelta(days=1)

    result = simulate_auto_select_portfolio(
        (cash_candidate, short_candidate),
        (
            _bar(),
            _bar(
                at=cash_exit_at,
                open=105,
                high=115,
                low=104,
                close=112,
            ),
            _bar(at=short_entry_at),
            _bar(
                at=short_exit_at,
                open=95,
                high=96,
                low=75,
                close=80,
            ),
        ),
        decisions=(cash_decision, short_decision),
    )

    assert result["trade_mode"] == "auto_select"
    assert result["scope"] == "auto_select_research_backtest"
    assert result["metrics"]["closed_trades"] == 2
    entry_modes = {
        details["mode"]
        for details in result["events"].query("event_type == 'entry'")["details"]
    }
    assert entry_modes == {"cash", "margin_short"}
    assert result["realized_pnl"] > 0
    assert result["manifest"]["auto_select_decisions"]
    assert result["real_order_sent"] is False


def test_auto_select_rejects_candidate_not_linked_to_selected_hash():
    candidate = _candidate(decision_id="decision-1")
    decision = select_trade_mode(
        decision_id="decision-1",
        decision_at=NOW,
        candidates=(
            _selection_candidate(
                TradeMode.CASH,
                stable_payload_hash({"cash": 1}),
                score=60,
            ),
            _selection_candidate(
                TradeMode.MARGIN_LONG,
                stable_payload_hash({"different": 1}),
                score=90,
            ),
            _selection_candidate(
                TradeMode.MARGIN_SHORT,
                stable_payload_hash({"short": 1}),
                score=70,
            ),
        ),
    )

    with pytest.raises(ValueError, match="analysis hash"):
        simulate_auto_select_portfolio(
            (candidate,),
            (_bar(),),
            decisions=(decision,),
        )


def test_runner_rejects_cross_mode_or_missing_entry_market_data():
    with pytest.raises(ValueError, match="isolated trade mode"):
        simulate_margin_mode_portfolio(
            (_candidate(TradeMode.MARGIN_SHORT),),
            (_bar(),),
            trade_mode=TradeMode.MARGIN_LONG,
        )
    with pytest.raises(ValueError, match="entry session"):
        simulate_margin_mode_portfolio(
            (_candidate(),),
            (),
            trade_mode=TradeMode.MARGIN_LONG,
        )
