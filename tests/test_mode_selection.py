from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.analysis.mode_selection import (
    AutoSelectDecision,
    AutoSelectPolicy,
    AutoSelectStatus,
    ModeCandidateRisk,
    ModeSelectionCandidate,
    select_trade_mode,
)
from app.analysis.trade_modes import EligibilityStatus, TradeMode
from app.backtest.audit import stable_payload_hash
from tests.test_trade_modes import NOW


def _candidate(mode: TradeMode, **changes) -> ModeSelectionCandidate:
    values = {
        "mode": mode,
        "input_hash": stable_payload_hash({"mode": mode.value, "fixture": "candidate"}),
        "data_as_of": NOW - timedelta(minutes=5),
        "eligibility_status": EligibilityStatus.ELIGIBLE,
        "analysis_status": "candidate",
        "pretrade_score": 70,
        "expected_risk_reward": 2.0,
        "risk_level": ModeCandidateRisk.MODERATE,
    }
    values.update(changes)
    return ModeSelectionCandidate(**values)


def _all_candidates(**by_mode) -> tuple[ModeSelectionCandidate, ...]:
    return tuple(
        _candidate(mode, **by_mode.get(mode.value, {}))
        for mode in (
            TradeMode.CASH,
            TradeMode.MARGIN_LONG,
            TradeMode.MARGIN_SHORT,
        )
    )


def test_auto_select_uses_frozen_pretrade_score_and_records_every_mode():
    decision = select_trade_mode(
        decision_id="decision-1",
        decision_at=NOW,
        candidates=_all_candidates(
            cash={"pretrade_score": 72},
            margin_long={"pretrade_score": 82},
            margin_short={"pretrade_score": 75},
        ),
    )

    assert decision.status == AutoSelectStatus.SELECTED
    assert decision.selected_mode == TradeMode.MARGIN_LONG
    assert len(decision.evaluations) == 3
    assert sum(item.selected for item in decision.evaluations) == 1
    assert not decision.real_order_sent


def test_auto_select_does_not_fall_back_to_cash_when_all_modes_fail():
    decision = select_trade_mode(
        decision_id="decision-2",
        decision_at=NOW,
        candidates=_all_candidates(
            cash={"pretrade_score": 40},
            margin_long={
                "eligibility_status": EligibilityStatus.INSUFFICIENT_DATA,
                "reason_codes": ("margin_snapshot_missing",),
            },
            margin_short={
                "eligibility_status": EligibilityStatus.NOT_ELIGIBLE,
                "reason_codes": ("short_inventory_unavailable",),
            },
        ),
    )

    assert decision.status == AutoSelectStatus.NO_ELIGIBLE_MODE
    assert decision.selected_mode is None
    evaluations = {item.mode: item for item in decision.evaluations}
    assert "pretrade_score_below_minimum" in evaluations[TradeMode.CASH].rejection_codes
    assert "mode_data_insufficient" in (
        evaluations[TradeMode.MARGIN_LONG].rejection_codes
    )
    assert "mode_not_eligible" in evaluations[TradeMode.MARGIN_SHORT].rejection_codes


def test_future_data_and_unapproved_review_are_explicit_rejections():
    decision = select_trade_mode(
        decision_id="decision-3",
        decision_at=NOW,
        candidates=_all_candidates(
            cash={"pretrade_score": 80},
            margin_long={
                "pretrade_score": 90,
                "data_as_of": NOW + timedelta(seconds=1),
            },
            margin_short={
                "pretrade_score": 85,
                "analysis_status": "warning",
                "human_review_required": True,
            },
        ),
    )

    assert decision.selected_mode == TradeMode.CASH
    evaluations = {item.mode: item for item in decision.evaluations}
    assert evaluations[TradeMode.MARGIN_LONG].rejection_codes == (
        "candidate_data_after_decision",
    )
    assert evaluations[TradeMode.MARGIN_SHORT].rejection_codes == (
        "human_review_not_approved",
    )


def test_approved_warning_can_compete_but_blocked_risk_cannot():
    decision = select_trade_mode(
        decision_id="decision-4",
        decision_at=NOW,
        candidates=_all_candidates(
            cash={"pretrade_score": 70},
            margin_long={
                "pretrade_score": 90,
                "analysis_status": "warning",
                "human_review_required": True,
                "human_review_approved": True,
            },
            margin_short={
                "pretrade_score": 95,
                "risk_level": ModeCandidateRisk.BLOCKED,
                "analysis_status": "blocked",
            },
        ),
    )

    assert decision.selected_mode == TradeMode.MARGIN_LONG
    short = next(
        item for item in decision.evaluations if item.mode == TradeMode.MARGIN_SHORT
    )
    assert "risk_level_blocked" in short.rejection_codes
    assert "analysis_status_blocked" in short.rejection_codes


def test_exact_tie_uses_conservative_cash_then_long_then_short_order():
    decision = select_trade_mode(
        decision_id="decision-5",
        decision_at=NOW,
        candidates=_all_candidates(),
    )

    assert decision.selected_mode == TradeMode.CASH


def test_selection_hash_is_order_independent_but_changes_with_frozen_input():
    candidates = _all_candidates()
    first = select_trade_mode(
        decision_id="decision-6",
        decision_at=NOW,
        candidates=candidates,
    )
    reordered = select_trade_mode(
        decision_id="decision-6",
        decision_at=NOW,
        candidates=tuple(reversed(candidates)),
    )
    changed = select_trade_mode(
        decision_id="decision-6",
        decision_at=NOW,
        candidates=_all_candidates(margin_long={"pretrade_score": 71}),
    )

    assert first.input_hash == reordered.input_hash
    assert first.input_hash != changed.input_hash


def test_auto_select_requires_all_three_modes_without_hidden_candidates():
    with pytest.raises(ValueError, match="exactly one"):
        select_trade_mode(
            decision_id="decision-7",
            decision_at=NOW,
            candidates=(_candidate(TradeMode.CASH),),
        )
    with pytest.raises(ValidationError, match="cannot be a candidate"):
        _candidate(TradeMode.AUTO_SELECT)


def test_candidate_schema_rejects_outcome_fields_and_invalid_review_state():
    with pytest.raises(ValidationError, match="Extra inputs"):
        ModeSelectionCandidate(
            **_candidate(TradeMode.CASH).model_dump(),
            realized_pnl=1_000_000,
        )
    with pytest.raises(ValidationError, match="review approval"):
        _candidate(TradeMode.CASH, human_review_approved=True)


def test_policy_requires_complete_unique_tie_order():
    with pytest.raises(ValidationError, match="tie order"):
        AutoSelectPolicy(
            conservative_tie_order=(TradeMode.CASH, TradeMode.MARGIN_LONG)
        )


def test_decision_schema_rejects_inconsistent_selection_and_real_order_flag():
    valid = select_trade_mode(
        decision_id="decision-schema",
        decision_at=NOW,
        candidates=_all_candidates(),
    )
    payload = valid.model_dump()
    payload["selected_mode"] = TradeMode.MARGIN_SHORT
    with pytest.raises(ValidationError, match="inconsistent"):
        AutoSelectDecision(**payload)

    payload = valid.model_dump()
    payload["real_order_sent"] = True
    with pytest.raises(ValidationError):
        AutoSelectDecision(**payload)
