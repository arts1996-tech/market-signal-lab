from datetime import UTC, datetime
import json
from pathlib import Path

import pandas as pd

from app.backtest.audit import stable_payload_hash


class ValidationWindowConflict(ValueError):
    """Raised when an already-claimed validation period would be reused."""


def _normalized_date(value) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return (
        timestamp.tz_localize("UTC")
        if timestamp.tz is None
        else timestamp.tz_convert("UTC")
    )


def _load_registry(path: Path) -> list[dict]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("validation registry must contain a JSON list")
    return payload


def _write_registry(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def forward_period_activation(
    path: str | Path, *, evaluation_track: str
) -> dict | None:
    entries = _load_registry(Path(path))
    matches = [
        entry
        for entry in entries
        if entry.get("claim_type") == "forward_period"
        and entry.get("evaluation_track") == evaluation_track
    ]
    if len(matches) > 1:
        raise ValueError("multiple forward-period activations exist for one track")
    return matches[0] if matches else None


def claim_forward_period(
    path: str | Path,
    *,
    strategy_version: str,
    rule_hash: str,
    frozen_rule_hash: str,
    evaluation_track: str,
    validation_end,
    forward_start,
    protocol_version: str,
    observed_through=None,
    claimed_at: datetime | None = None,
) -> dict:
    """Freeze the boundary after which observations are forward-only."""

    if not all(
        [
            strategy_version,
            rule_hash,
            frozen_rule_hash,
            evaluation_track,
            protocol_version,
        ]
    ):
        raise ValueError("forward-period activation fields are required")
    validation_end_value = _normalized_date(validation_end).normalize()
    forward_start_value = _normalized_date(forward_start).normalize()
    if forward_start_value <= validation_end_value:
        raise ValueError("forward period must start after validation ends")
    observed_through_value = _normalized_date(
        observed_through if observed_through is not None else validation_end
    ).normalize()
    if observed_through_value < validation_end_value:
        raise ValueError("observed_through must not be before validation ends")
    if forward_start_value <= observed_through_value:
        raise ValueError("forward period must start after all already-observed data")
    registry_path = Path(path)
    entries = _load_registry(registry_path)
    deterministic = {
        "claim_type": "forward_period",
        "strategy_version": strategy_version,
        "rule_hash": rule_hash,
        "frozen_rule_hash": frozen_rule_hash,
        "evaluation_track": evaluation_track,
        "validation_end": validation_end_value.isoformat(),
        "observed_through": observed_through_value.isoformat(),
        "forward_start": forward_start_value.isoformat(),
        "protocol_version": protocol_version,
    }
    claim_id = stable_payload_hash(deterministic)
    for entry in entries:
        if entry.get("claim_id") == claim_id:
            return entry
        if (
            entry.get("claim_type") == "forward_period"
            and entry.get("evaluation_track") == evaluation_track
        ):
            raise ValidationWindowConflict(
                "forward period is already frozen for this evaluation track"
            )
    claim = {
        **deterministic,
        "claim_id": claim_id,
        "claimed_at": (claimed_at or datetime.now(UTC)).isoformat(),
        "immutable": True,
    }
    entries.append(claim)
    _write_registry(registry_path, entries)
    return claim


def claim_validation_window(
    path: str | Path,
    *,
    strategy_version: str,
    rule_hash: str,
    evaluation_track: str = "default",
    test_start,
    test_end,
    train_start=None,
    train_end=None,
    training_input_hash: str | None = None,
    frozen_rule_hash: str | None = None,
    protocol_version: str | None = None,
    claimed_at: datetime | None = None,
) -> dict:
    """Permanently claim an unseen period for one frozen rule configuration.

    Repeating the identical claim is idempotent. An overlapping claim with a
    different rule hash is rejected so a strategy cannot be changed after
    observing the period and then evaluated on the same observations again.
    """

    registry_path = Path(path)
    start = _normalized_date(test_start).normalize()
    end = _normalized_date(test_end).normalize()
    if not strategy_version or not rule_hash or not evaluation_track:
        raise ValueError("strategy_version, rule_hash, and evaluation_track are required")
    if end < start:
        raise ValueError("test_end must not be before test_start")
    training_values = (train_start, train_end, training_input_hash)
    if any(value is not None for value in training_values):
        if any(value is None for value in training_values):
            raise ValueError(
                "train_start, train_end, and training_input_hash must be supplied together"
            )
        normalized_train_start = _normalized_date(train_start).normalize()
        normalized_train_end = _normalized_date(train_end).normalize()
        if normalized_train_end < normalized_train_start:
            raise ValueError("train_end must not be before train_start")
        if normalized_train_end >= start:
            raise ValueError("training period must end before validation starts")
    else:
        normalized_train_start = None
        normalized_train_end = None

    entries = _load_registry(registry_path)
    deterministic = {
        "strategy_version": strategy_version,
        "rule_hash": rule_hash,
        "evaluation_track": evaluation_track,
        "test_start": start.isoformat(),
        "test_end": end.isoformat(),
    }
    if normalized_train_start is not None:
        deterministic.update(
            {
                "train_start": normalized_train_start.isoformat(),
                "train_end": normalized_train_end.isoformat(),
                "training_input_hash": str(training_input_hash),
                "frozen_rule_hash": str(frozen_rule_hash or rule_hash),
                "protocol_version": str(protocol_version or "unspecified"),
            }
        )
    claim_id = stable_payload_hash(deterministic)
    for entry in entries:
        if entry.get("claim_id") == claim_id:
            return entry
        if entry.get("claim_type") == "forward_period":
            if entry.get("evaluation_track") == evaluation_track:
                forward_start = _normalized_date(entry["forward_start"]).normalize()
                if end >= forward_start:
                    raise ValidationWindowConflict(
                        "validation period cannot consume observations reserved for forward evaluation"
                    )
            continue
        existing_start = _normalized_date(entry["test_start"]).normalize()
        existing_end = _normalized_date(entry["test_end"]).normalize()
        overlaps = start <= existing_end and end >= existing_start
        same_track = entry.get("evaluation_track", "default") == evaluation_track
        if same_track and overlaps:
            different_rule = (
                entry.get("rule_hash") != rule_hash
                or entry.get("strategy_version") != strategy_version
                or (
                    entry.get("frozen_rule_hash") is not None
                    and frozen_rule_hash is not None
                    and entry.get("frozen_rule_hash") != frozen_rule_hash
                )
            )
            if different_rule:
                raise ValidationWindowConflict(
                    "validation period overlaps a window already observed by a different frozen rule"
                )
            same_window = existing_start == start and existing_end == end
            revised_training = (
                same_window
                and entry.get("training_input_hash") is not None
                and training_input_hash is not None
                and entry.get("training_input_hash") != training_input_hash
            )
            if revised_training:
                raise ValidationWindowConflict(
                    "training input changed after the validation window was frozen"
                )

    claim = {
        **deterministic,
        "claim_type": "historical_validation",
        "claim_id": claim_id,
        "claimed_at": (claimed_at or datetime.now(UTC)).isoformat(),
        "immutable": True,
    }
    entries.append(claim)
    _write_registry(registry_path, entries)
    return claim
