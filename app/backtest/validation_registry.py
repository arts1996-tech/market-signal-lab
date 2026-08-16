from datetime import UTC, datetime
import json
from pathlib import Path

import pandas as pd

from app.backtest.audit import stable_payload_hash


class ValidationWindowConflict(ValueError):
    """Raised when an already-claimed validation period would be reused."""


def _normalized_date(value) -> pd.Timestamp:
    return pd.Timestamp(value).tz_localize("UTC") if pd.Timestamp(value).tz is None else pd.Timestamp(value).tz_convert("UTC")


def _load_registry(path: Path) -> list[dict]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("validation registry must contain a JSON list")
    return payload


def claim_validation_window(
    path: str | Path,
    *,
    strategy_version: str,
    rule_hash: str,
    test_start,
    test_end,
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
    if not strategy_version or not rule_hash:
        raise ValueError("strategy_version and rule_hash are required")
    if end < start:
        raise ValueError("test_end must not be before test_start")

    entries = _load_registry(registry_path)
    deterministic = {
        "strategy_version": strategy_version,
        "rule_hash": rule_hash,
        "test_start": start.isoformat(),
        "test_end": end.isoformat(),
    }
    claim_id = stable_payload_hash(deterministic)
    for entry in entries:
        if entry.get("claim_id") == claim_id:
            return entry
        existing_start = _normalized_date(entry["test_start"]).normalize()
        existing_end = _normalized_date(entry["test_end"]).normalize()
        overlaps = start <= existing_end and end >= existing_start
        if overlaps and entry.get("rule_hash") != rule_hash:
            raise ValidationWindowConflict(
                "validation period overlaps a window already observed by a different rule"
            )

    claim = {
        **deterministic,
        "claim_id": claim_id,
        "claimed_at": (claimed_at or datetime.now(UTC)).isoformat(),
        "immutable": True,
    }
    entries.append(claim)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = registry_path.with_suffix(f"{registry_path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary_path.replace(registry_path)
    return claim
