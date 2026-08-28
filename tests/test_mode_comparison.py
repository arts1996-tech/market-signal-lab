import pandas as pd
import pytest

from app.analysis.mode_selection import select_trade_mode
from app.analysis.trade_modes import TradeMode
from app.backtest.mode_comparison import build_mode_backtest_comparison
from tests.test_mode_selection import _all_candidates
from tests.test_trade_modes import NOW


def _cash_result(**changes):
    result = {
        "status": "success",
        "manifest": {"run_id": "cash-run"},
        "metrics": {
            "total_return": 0.05,
            "maximum_drawdown": -0.08,
            "closed_trades": 4,
            "win_rate": 0.5,
            "benchmark_return": 0.03,
            "excess_return": 0.02,
        },
        "transactions": pd.DataFrame(
            {"fee": [100.0, 120.0], "tax": [0.0, 0.0]}
        ),
        "rejected_signals": pd.DataFrame([{"reason": "no_fill"}]),
        "quality_warnings": ["cash-research"],
    }
    result.update(changes)
    return result


def _margin_result(mode: TradeMode, **changes):
    result = {
        "status": "success",
        "trade_mode": mode.value,
        "manifest": {"run_id": f"{mode.value}-run"},
        "metrics": {
            "total_return": 0.04,
            "maximum_drawdown": -0.12,
            "closed_trades": 3,
            "win_rate": 2 / 3,
            "gross_fees": 300.0,
            "financing_cost": 80.0,
            "forced_liquidations": 1,
            "rejected_entries": 2,
            "deferred_exits": 1,
            "benchmark_return": 0.03,
            "excess_return": 0.01,
        },
        "quality_warnings": ["research_only"],
    }
    result.update(changes)
    return result


def _decision():
    return select_trade_mode(
        decision_id="decision-1",
        decision_at=NOW,
        candidates=_all_candidates(margin_long={"pretrade_score": 80}),
    )


def test_comparison_normalizes_three_modes_without_using_performance_for_selection():
    comparison = build_mode_backtest_comparison(
        cash_result=_cash_result(),
        margin_long_result=_margin_result(TradeMode.MARGIN_LONG),
        margin_short_result=_margin_result(
            TradeMode.MARGIN_SHORT,
            metrics={
                **_margin_result(TradeMode.MARGIN_SHORT)["metrics"],
                "total_return": 0.90,
            },
        ),
        auto_select_decisions=(_decision(),),
    )

    assert comparison.auto_select_series_reason_codes == (
        "auto_select_series_not_supplied",
        "corporate_action_coverage_not_verified",
    )

    summaries = {item.mode: item for item in comparison.summaries}
    assert set(summaries) == {
        TradeMode.CASH,
        TradeMode.MARGIN_LONG,
        TradeMode.MARGIN_SHORT,
    }
    assert summaries[TradeMode.CASH].reported_cost == 220
    assert summaries[TradeMode.MARGIN_LONG].reported_cost == 380
    assert summaries[TradeMode.MARGIN_LONG].forced_liquidations == 1
    assert summaries[TradeMode.MARGIN_LONG].unfilled_or_rejected == 3
    assert comparison.auto_select_decisions[0].selected_mode == TradeMode.MARGIN_LONG
    assert comparison.auto_select_series_status == "selection_ready_execution_pending"
    assert comparison.real_order_sent is False


def test_missing_mixed_series_is_explicit_not_silently_replaced_by_best_mode():
    comparison = build_mode_backtest_comparison(
        cash_result=_cash_result(),
        margin_long_result=_margin_result(TradeMode.MARGIN_LONG),
        margin_short_result=_margin_result(TradeMode.MARGIN_SHORT),
        auto_select_decisions=(_decision(),),
    )

    assert len(comparison.summaries) == 3
    assert all(summary.mode != TradeMode.AUTO_SELECT for summary in comparison.summaries)
    assert comparison.auto_select_series_status == "selection_ready_execution_pending"
    assert "auto_select_series_not_supplied" in (
        comparison.auto_select_series_reason_codes
    )


def test_completed_auto_series_is_a_fourth_separate_summary():
    auto_result = _margin_result(
        TradeMode.AUTO_SELECT,
        reason_codes=[],
    )
    comparison = build_mode_backtest_comparison(
        cash_result=_cash_result(),
        margin_long_result=_margin_result(TradeMode.MARGIN_LONG),
        margin_short_result=_margin_result(TradeMode.MARGIN_SHORT),
        auto_select_decisions=(_decision(),),
        auto_select_result=auto_result,
    )

    assert {item.mode for item in comparison.summaries} == set(TradeMode)
    assert comparison.auto_select_series_status == "success"
    assert comparison.auto_select_series_reason_codes == ()


def test_comparison_hash_is_stable_for_reordered_decisions():
    first_decision = _decision()
    second_decision = select_trade_mode(
        decision_id="decision-2",
        decision_at=NOW,
        candidates=_all_candidates(cash={"pretrade_score": 85}),
    )
    inputs = {
        "cash_result": _cash_result(),
        "margin_long_result": _margin_result(TradeMode.MARGIN_LONG),
        "margin_short_result": _margin_result(TradeMode.MARGIN_SHORT),
    }

    first = build_mode_backtest_comparison(
        **inputs,
        auto_select_decisions=(first_decision, second_decision),
    )
    reordered = build_mode_backtest_comparison(
        **inputs,
        auto_select_decisions=(second_decision, first_decision),
    )

    assert first.input_hash == reordered.input_hash


def test_comparison_rejects_mislabeled_margin_or_auto_results():
    with pytest.raises(ValueError, match="mismatched trade_mode"):
        build_mode_backtest_comparison(
            cash_result=_cash_result(),
            margin_long_result=_margin_result(
                TradeMode.MARGIN_LONG,
                trade_mode="margin_short",
            ),
            margin_short_result=_margin_result(TradeMode.MARGIN_SHORT),
            auto_select_decisions=(),
        )
    with pytest.raises(ValueError, match="auto_select"):
        build_mode_backtest_comparison(
            cash_result=_cash_result(),
            margin_long_result=_margin_result(TradeMode.MARGIN_LONG),
            margin_short_result=_margin_result(TradeMode.MARGIN_SHORT),
            auto_select_decisions=(),
            auto_select_result={"trade_mode": "cash"},
        )
