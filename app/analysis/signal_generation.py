from __future__ import annotations

from typing import Any

import pandas as pd

from app.analysis.market_calendar import next_exchange_session
from app.analysis.movement_candidates import build_movement_candidates


SIGNAL_GENERATION_VERSION = "movement-as-of-v1"
FUTURE_OUTCOME_FIELDS = {
    "entry_price",
    "exit_date",
    "exit_price",
    "return",
    "outcome",
    "outcome_reasons",
    "realized_pnl",
    "unrealized_pnl",
}


def _utc_timestamp(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _known_prices_as_of(prices: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """Return only observations that were knowable at ``as_of``."""

    if prices.empty:
        return prices.copy()
    if "price_time" not in prices:
        raise ValueError("price input must contain price_time")

    frame = prices.copy()
    price_time = pd.to_datetime(frame["price_time"], utc=True, errors="coerce")
    known = price_time.notna() & (price_time <= as_of)
    availability_column = next(
        (column for column in ("available_at", "fetched_at") if column in frame),
        None,
    )
    if availability_column is not None:
        available_at = pd.to_datetime(
            frame[availability_column], utc=True, errors="coerce"
        )
        known &= available_at.notna() & (available_at <= as_of)
    return frame.loc[known].copy()


def _input_warnings(index_prices: pd.DataFrame, japan_prices: pd.DataFrame) -> list[str]:
    warnings: list[str] = []
    for label, frame in (("index_prices", index_prices), ("japan_prices", japan_prices)):
        if not frame.empty and not any(
            column in frame for column in ("available_at", "fetched_at")
        ):
            warnings.append(f"{label}: availability_timestamp_missing")
    return warnings


def generate_signals_as_of(
    index_prices: pd.DataFrame,
    japan_prices: pd.DataFrame,
    *,
    as_of: Any,
    score_threshold: int = 70,
    min_observations: int = 30,
    limit: int = 20,
    stop_loss: float = -0.05,
    take_profit: float = 0.08,
    maximum_holding_days: int = 10,
) -> dict:
    """Generate point-in-time decisions without requiring future outcomes.

    Every price and availability timestamp is cut off at ``as_of`` before any
    indicator or market-context calculation. Returned execution signals contain
    no outcome, return, exit, or execution-price fields.
    """

    if not 0 <= score_threshold <= 100:
        raise ValueError("score_threshold must be between 0 and 100")
    if min_observations < 1 or limit < 1 or maximum_holding_days < 1:
        raise ValueError("observation, limit, and holding-day values must be positive")
    if stop_loss >= 0 or take_profit <= 0:
        raise ValueError("stop_loss must be negative and take_profit must be positive")

    decision_at = _utc_timestamp(as_of)
    known_index_prices = _known_prices_as_of(index_prices, decision_at)
    known_japan_prices = _known_prices_as_of(japan_prices, decision_at)
    quality_warnings = _input_warnings(index_prices, japan_prices)

    movement = build_movement_candidates(
        known_index_prices,
        known_japan_prices,
        min_observations=min_observations,
        limit=limit,
    )
    decision_session = decision_at.tz_convert("Asia/Tokyo").normalize()
    entry_date = next_exchange_session(decision_session, "XTKS")
    if entry_date is not None:
        entry_date = _utc_timestamp(entry_date).normalize()

    decisions: list[dict] = []
    signals: list[dict] = []
    candidates = movement.get("candidates", pd.DataFrame())
    for candidate in candidates.to_dict(orient="records"):
        score = int(candidate.get("score", 0))
        direction = str(candidate.get("direction", "方向感は未確定"))
        if score < score_threshold:
            status = "below_score_threshold"
            decision = "待機"
            reason_code = "score_below_threshold"
        elif "上方向" not in direction:
            status = "observe_only"
            decision = "待機"
            reason_code = "long_entry_direction_not_confirmed"
        elif entry_date is None:
            status = "insufficient_data"
            decision = "データ不足"
            reason_code = "next_exchange_session_unavailable"
        else:
            status = "eligible_signal"
            decision = "買い候補"
            reason_code = "score_and_direction_gate_passed"

        data_as_of = candidate.get("data_as_of")
        common = {
            "decision_at": decision_at,
            "data_as_of": data_as_of,
            "symbol": candidate.get("symbol"),
            "name": candidate.get("name", candidate.get("symbol")),
            "score": score,
            "direction": direction,
            "decision": decision,
            "status": status,
            "reason_code": reason_code,
            "reasons": list(candidate.get("reasons") or []),
            "quality_warnings": list(quality_warnings),
        }
        decisions.append(common)
        if status == "eligible_signal":
            signals.append(
                {
                    **common,
                    "signal_date": decision_at,
                    "entry_date": entry_date,
                    "side": "long",
                    "minimum_score": score_threshold,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "maximum_holding_days": maximum_holding_days,
                    "counterarguments": [
                        "財務・イベント・板情報を完全には反映していません",
                        "遅延または過去価格による判断は"
                        "現在の売買判断ではありません",
                    ],
                }
            )

    insufficient = movement.get("insufficient", pd.DataFrame()).copy()
    for item in insufficient.to_dict(orient="records"):
        decisions.append(
            {
                "decision_at": decision_at,
                "data_as_of": item.get("data_as_of"),
                "symbol": item.get("symbol"),
                "name": item.get("name", item.get("symbol")),
                "score": None,
                "direction": "不明",
                "decision": "データ不足",
                "status": "insufficient_data",
                "reason_code": item.get("reason", "insufficient_price_history"),
                "reasons": [],
                "quality_warnings": list(quality_warnings),
            }
        )

    signal_frame = pd.DataFrame(signals)
    leaked = FUTURE_OUTCOME_FIELDS.intersection(signal_frame.columns)
    if leaked:
        raise AssertionError(f"future outcome fields leaked into signals: {sorted(leaked)}")

    decision_frame = pd.DataFrame(decisions)
    if not signal_frame.empty:
        observation_status = "eligible_signals"
    elif not decision_frame.empty and set(decision_frame["status"]) == {"insufficient_data"}:
        observation_status = "insufficient_data"
    else:
        observation_status = "no_eligible_signals"

    insufficient_count = (
        int((decision_frame["status"] == "insufficient_data").sum())
        if not decision_frame.empty
        else 0
    )
    return {
        "generation_version": SIGNAL_GENERATION_VERSION,
        "decision_at": decision_at,
        "observation_status": observation_status,
        "signals": signal_frame,
        "decisions": decision_frame,
        "insufficient": insufficient,
        "known_index_prices": known_index_prices,
        "known_japan_prices": known_japan_prices,
        "quality_warnings": quality_warnings,
        "summary": {
            "eligible_signals": len(signal_frame),
            "decisions": len(decision_frame),
            "insufficient": insufficient_count,
        },
    }
