from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from app.analysis.market_calendar import exchange_calendar
from app.backtest.audit import frame_hash, stable_payload_hash


DECISION_TRACK_DELAYED = "delayed_historical"
DECISION_TRACK_CURRENT = "current_market"
DECISION_TRACKS = {DECISION_TRACK_DELAYED, DECISION_TRACK_CURRENT}
CURRENT_MARKET_MAX_SESSION_DELAY = 1


def _utc_timestamp(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _latest_session(frame: pd.DataFrame) -> date | None:
    if frame.empty or "price_time" not in frame:
        return None
    values = pd.to_datetime(frame["price_time"], utc=True, errors="coerce").dropna()
    return None if values.empty else values.max().date()


def _sources(*frames: pd.DataFrame) -> list[str]:
    values: set[str] = set()
    for frame in frames:
        if not frame.empty and "source" in frame:
            values.update(str(value) for value in frame["source"].dropna().unique())
    return sorted(values)


def _exchange_session_delay(latest_session: date | None, observed_at: Any) -> int | None:
    if latest_session is None:
        return None
    observed_date = _utc_timestamp(observed_at).tz_convert("Asia/Tokyo").date()
    if latest_session > observed_date:
        raise ValueError("latest price session cannot be after observation date")
    sessions = exchange_calendar("XTKS").sessions_in_range(
        pd.Timestamp(latest_session), pd.Timestamp(observed_date)
    )
    return sum(session.date() > latest_session for session in sessions)


def build_decision_observation(
    signal_generation: dict,
    index_prices: pd.DataFrame,
    japan_prices: pd.DataFrame,
    *,
    decision_track: str,
    observed_at: Any,
) -> dict:
    if decision_track not in DECISION_TRACKS:
        raise ValueError(f"unsupported decision_track: {decision_track}")
    observed = _utc_timestamp(observed_at)
    latest_by_input = {
        "index_prices": _latest_session(index_prices),
        "japan_prices": _latest_session(japan_prices),
    }
    available_latest = [value for value in latest_by_input.values() if value is not None]
    price_latest_session = min(available_latest) if available_latest else None
    delay = _exchange_session_delay(price_latest_session, observed)
    observation_status = str(signal_generation.get("observation_status") or "unknown")
    decisions = signal_generation.get("decisions", pd.DataFrame())
    reasons: list[str] = []

    if any(value is None for value in latest_by_input.values()):
        reasons.append("price_data_missing")
    if observation_status == "insufficient_data":
        reasons.append("insufficient_price_history")
    elif observation_status == "no_eligible_signals":
        reasons.append("no_eligible_candidates")
    elif isinstance(decisions, pd.DataFrame) and decisions.empty:
        reasons.append("no_candidates_generated")

    if decision_track == DECISION_TRACK_CURRENT:
        if delay is None or delay > CURRENT_MARKET_MAX_SESSION_DELAY:
            reasons.append("current_market_freshness_failed")
        blocking = {
            "price_data_missing",
            "insufficient_price_history",
            "current_market_freshness_failed",
        }
        quality_gate_status = "blocked" if blocking.intersection(reasons) else (
            "no_action" if reasons else "passed"
        )
    else:
        reasons.append("delayed_data_research_only")
        quality_gate_status = "research_only"

    source_values = _sources(index_prices, japan_prices)
    input_hash = stable_payload_hash(
        {
            "decision_track": decision_track,
            "generation_version": signal_generation.get("generation_version"),
            "index_price_hash": frame_hash(index_prices),
            "japan_price_hash": frame_hash(japan_prices),
            "decisions_hash": frame_hash(decisions) if isinstance(decisions, pd.DataFrame) else None,
        }
    )
    return {
        "decision_track": decision_track,
        "observed_at": observed,
        "price_latest_session": price_latest_session,
        "data_delay_days": delay,
        "data_delay_basis": "XTKS_sessions_after_latest_price_v1",
        "data_sources": source_values,
        "source_latest_sessions": {
            key: None if value is None else value.isoformat()
            for key, value in latest_by_input.items()
        },
        "input_hash": input_hash,
        "quality_gate_status": quality_gate_status,
        "quality_gate_reasons": list(dict.fromkeys(reasons)),
    }


def prepare_decision_track_inputs(
    signal_generation: dict,
    index_prices: pd.DataFrame,
    japan_prices: pd.DataFrame,
    *,
    decision_track: str,
    observed_at: Any,
) -> dict:
    observation = build_decision_observation(
        signal_generation,
        index_prices,
        japan_prices,
        decision_track=decision_track,
        observed_at=observed_at,
    )
    signals = signal_generation.get("signals", pd.DataFrame()).copy()
    decisions = signal_generation.get("decisions", pd.DataFrame()).copy()
    for frame in (signals, decisions):
        if not frame.empty:
            frame["decision_track"] = decision_track
            frame["quality_gate_status"] = observation["quality_gate_status"]
            frame["quality_gate_reasons"] = [
                observation["quality_gate_reasons"] for _ in range(len(frame))
            ]

    if decision_track == DECISION_TRACK_DELAYED:
        if not decisions.empty and "status" in decisions:
            eligible = decisions["status"] == "eligible_signal"
            decisions.loc[eligible, "decision"] = "研究上の買い候補"
    elif observation["quality_gate_status"] != "passed":
        signals = signals.iloc[0:0].copy()
        if not decisions.empty:
            eligible = decisions["status"] == "eligible_signal"
            decisions.loc[eligible, "decision"] = "データ不足"
            decisions.loc[eligible, "status"] = "quality_gate_blocked"
            reason = (
                "current_market_freshness_failed"
                if "current_market_freshness_failed"
                in observation["quality_gate_reasons"]
                else observation["quality_gate_reasons"][0]
                if observation["quality_gate_reasons"]
                else "quality_gate_not_passed"
            )
            decisions.loc[eligible, "reason_code"] = reason

    return {
        "observation": observation,
        "signals": signals,
        "decisions": decisions,
    }
