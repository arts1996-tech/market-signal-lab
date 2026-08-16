from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any

import pandas as pd


STRATEGY_VERSION = "phase4-long-only-v0.3"
EXECUTION_VERSION = "ohlc-next-open-conservative-v2"
DECISION_CARD_VERSION = "decision-card-v2"


def _json_value(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Decimal):
        return float(value)
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        return value.item()
    return value


def stable_payload_hash(payload: Any) -> str:
    canonical = json.dumps(
        _json_value(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def frame_hash(frame: pd.DataFrame, columns: list[str] | None = None) -> str:
    if frame.empty:
        return stable_payload_hash([])
    selected_columns = [
        column for column in (columns or list(frame.columns)) if column in frame
    ]
    records = [
        {column: _json_value(row[column]) for column in selected_columns}
        for row in frame[selected_columns].to_dict(orient="records")
    ]
    canonical_records = sorted(
        records,
        key=lambda record: json.dumps(
            record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ),
    )
    return stable_payload_hash(canonical_records)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    try:
        if pd.isna(value):
            return []
    except (TypeError, ValueError):
        pass
    return [str(value)]


def build_run_manifest(
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    *,
    account_name: str,
    assumptions: Any,
    risk_rules: Any,
    strategy_version: str = STRATEGY_VERSION,
    execution_version: str = EXECUTION_VERSION,
    input_data_version: str | None = None,
    created_at: datetime | None = None,
) -> dict:
    signal_hash = frame_hash(signals)
    price_columns = [
        "price_time",
        "symbol",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "source",
        "source_symbol",
        "price_basis",
        "data_quality_status",
        "adjustment_factor",
        "available_at",
        "fetched_at",
        "tradable",
        "suspended",
        "limit_up",
        "limit_down",
        "special_quote",
    ]
    price_hash = frame_hash(prices, price_columns)
    deterministic = {
        "account_name": account_name,
        "strategy_version": strategy_version,
        "execution_version": execution_version,
        "input_data_version": input_data_version or price_hash,
        "signal_hash": signal_hash,
        "price_hash": price_hash,
        "assumptions": _json_value(assumptions),
        "risk_rules": _json_value(risk_rules),
    }
    return {
        **deterministic,
        "run_id": stable_payload_hash(deterministic),
        "created_at": (created_at or datetime.now(UTC)).isoformat(),
    }


def decision_card(
    signal: dict,
    *,
    status: str,
    manifest: dict,
    entry_price: float | None = None,
    exit_price: float | None = None,
    outcome_reason: str | None = None,
    quality_warnings: list[str] | None = None,
    event_at: Any | None = None,
) -> dict:
    stop_loss = float(signal.get("stop_loss", -0.05))
    take_profit = float(signal.get("take_profit", 0.08))
    risk_reward = take_profit / abs(stop_loss) if stop_loss < 0 else None
    warnings = _string_list(signal.get("quality_warnings")) + _string_list(
        quality_warnings
    )
    card_key = {
        "run_id": manifest["run_id"],
        "symbol": signal.get("symbol"),
        "signal_date": signal.get("signal_date"),
        "entry_date": signal.get("entry_date"),
    }
    card_id = stable_payload_hash(card_key)
    event_time = event_at or signal.get("entry_date") or signal.get("signal_date")
    event_key = {
        "card_id": card_id,
        "status": status,
        "event_at": event_time,
        "outcome_reason": outcome_reason,
    }
    return {
        "card_id": card_id,
        "event_id": stable_payload_hash(event_key),
        "card_version": DECISION_CARD_VERSION,
        "run_id": manifest["run_id"],
        "strategy_version": manifest["strategy_version"],
        "execution_version": manifest["execution_version"],
        "input_data_version": manifest["input_data_version"],
        "status": status,
        "event_at": event_time,
        "symbol": signal.get("symbol"),
        "name": signal.get("name", signal.get("symbol")),
        "data_as_of": signal.get("signal_date"),
        "entry_date": signal.get("entry_date"),
        "entry_condition": f"score >= {signal.get('minimum_score', '-')}",
        "entry_price": entry_price,
        "entry_price_range": None if entry_price is None else [entry_price, entry_price],
        "take_profit_rate": take_profit,
        "take_profit_price": (
            None if entry_price is None else entry_price * (1 + take_profit)
        ),
        "stop_loss_rate": stop_loss,
        "stop_loss_price": None if entry_price is None else entry_price * (1 + stop_loss),
        "invalidation_condition": "stop_loss_price以下、またはデータ品質ゲート不合格",
        "maximum_holding_days": int(signal.get("maximum_holding_days", 0)),
        "risk_reward": risk_reward,
        "confidence": "unrated",
        "similar_sample_count": signal.get("similar_sample_count"),
        "reference_quantity": signal.get("reference_quantity"),
        "planned_risk": signal.get("planned_risk"),
        "reasons": _string_list(signal.get("reasons")),
        "counterarguments": _string_list(signal.get("counterarguments")),
        "quality_warnings": list(dict.fromkeys(warnings)),
        "human_review_required": True,
        "exit_price": exit_price,
        "outcome_reason": outcome_reason,
    }
