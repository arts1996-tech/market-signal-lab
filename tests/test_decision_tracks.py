import pandas as pd
import pytest

from app.analysis.decision_tracks import (
    DECISION_TRACK_CURRENT,
    DECISION_TRACK_DELAYED,
    build_decision_observation,
    prepare_decision_track_inputs,
)
from app.services.forward_account_ledger import advance_and_persist_forward_accounts


def _generation(status: str = "eligible_signals") -> dict:
    decision_status = (
        "insufficient_data" if status == "insufficient_data" else "eligible_signal"
    )
    decisions = pd.DataFrame(
        [
            {
                "decision_at": pd.Timestamp("2026-08-14T09:00:00Z"),
                "symbol": "A",
                "decision": "買い候補",
                "status": decision_status,
                "reason_code": "test",
            }
        ]
    )
    signals = pd.DataFrame(
        [
            {
                "signal_date": pd.Timestamp("2026-08-14T09:00:00Z"),
                "entry_date": pd.Timestamp("2026-08-17T00:00:00Z"),
                "symbol": "A",
                "side": "long",
            }
        ]
    )
    if status == "insufficient_data":
        signals = signals.iloc[0:0]
    return {
        "generation_version": "test-v1",
        "observation_status": status,
        "signals": signals,
        "decisions": decisions,
    }


def _prices(day: str, source: str) -> pd.DataFrame:
    return pd.DataFrame(
        [{"price_time": pd.Timestamp(day, tz="UTC"), "symbol": source, "source": source}]
    )


def test_delayed_track_is_explicitly_research_only_but_preserves_research_signal():
    prepared = prepare_decision_track_inputs(
        _generation(),
        _prices("2026-05-15", "fred"),
        _prices("2026-05-15", "jquants"),
        decision_track=DECISION_TRACK_DELAYED,
        observed_at="2026-08-14T09:00:00Z",
    )

    observation = prepared["observation"]
    assert observation["quality_gate_status"] == "research_only"
    assert observation["data_delay_days"] > 30
    assert "delayed_data_research_only" in observation["quality_gate_reasons"]
    assert len(prepared["signals"]) == 1
    assert prepared["decisions"].iloc[0]["decision"] == "研究上の買い候補"


def test_stale_prices_are_blocked_from_current_market_entry_signals():
    prepared = prepare_decision_track_inputs(
        _generation(),
        _prices("2026-05-15", "fred"),
        _prices("2026-05-15", "jquants"),
        decision_track=DECISION_TRACK_CURRENT,
        observed_at="2026-08-14T09:00:00Z",
    )

    assert prepared["observation"]["quality_gate_status"] == "blocked"
    assert "current_market_freshness_failed" in prepared["observation"][
        "quality_gate_reasons"
    ]
    assert prepared["signals"].empty
    assert prepared["decisions"].iloc[0]["status"] == "quality_gate_blocked"
    assert prepared["decisions"].iloc[0]["decision"] == "データ不足"


def test_fresh_prices_can_pass_current_market_track():
    observation = build_decision_observation(
        _generation(),
        _prices("2026-08-14", "fred"),
        _prices("2026-08-14", "jquants"),
        decision_track=DECISION_TRACK_CURRENT,
        observed_at="2026-08-14T09:00:00Z",
    )

    assert observation["quality_gate_status"] == "passed"
    assert observation["data_delay_days"] == 0
    assert observation["data_sources"] == ["fred", "jquants"]


def test_missing_prices_and_insufficient_history_have_distinct_reason_codes():
    observation = build_decision_observation(
        _generation("insufficient_data"),
        pd.DataFrame(),
        _prices("2026-08-14", "jquants"),
        decision_track=DECISION_TRACK_CURRENT,
        observed_at="2026-08-14T09:00:00Z",
    )

    assert observation["quality_gate_status"] == "blocked"
    assert "price_data_missing" in observation["quality_gate_reasons"]
    assert "insufficient_price_history" in observation["quality_gate_reasons"]


def test_no_candidate_is_recorded_as_no_action_not_as_data_failure():
    generation = _generation("no_eligible_signals")
    generation["signals"] = generation["signals"].iloc[0:0]
    generation["decisions"].loc[0, "status"] = "below_score_threshold"
    observation = build_decision_observation(
        generation,
        _prices("2026-08-14", "fred"),
        _prices("2026-08-14", "jquants"),
        decision_track=DECISION_TRACK_CURRENT,
        observed_at="2026-08-14T09:00:00Z",
    )

    assert observation["quality_gate_status"] == "no_action"
    assert observation["quality_gate_reasons"] == ["no_eligible_candidates"]


def test_ledger_orchestrator_rejects_current_signals_when_quality_did_not_pass():
    blocked = build_decision_observation(
        _generation(),
        _prices("2026-05-15", "fred"),
        _prices("2026-05-15", "jquants"),
        decision_track=DECISION_TRACK_CURRENT,
        observed_at="2026-08-14T09:00:00Z",
    )

    with pytest.raises(ValueError, match="requires a passed quality gate"):
        advance_and_persist_forward_accounts(
            None,
            _generation()["signals"],
            pd.DataFrame(),
            observation=blocked,
        )
