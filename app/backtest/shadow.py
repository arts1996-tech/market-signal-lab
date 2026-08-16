from datetime import UTC, date, datetime
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import pandas as pd


def _serializable(value: Any) -> Any:
    if isinstance(value, pd.DataFrame):
        return [_serializable(item) for item in value.to_dict(orient="records")]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): _serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serializable(item) for item in value]
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        return value.item()
    return value


def write_forward_shadow_snapshot(
    output_dir: str | Path,
    result: dict,
    *,
    as_of: datetime | pd.Timestamp | None = None,
    daily: bool = False,
) -> Path:
    """Write one immutable, idempotent forward-shadow JSON snapshot."""

    manifest = result.get("manifest") or {}
    run_id = manifest.get("run_id")
    if not run_id:
        raise ValueError("result manifest must contain run_id")
    observed_at = pd.Timestamp(as_of or datetime.now(UTC))
    if observed_at.tzinfo is None:
        observed_at = observed_at.tz_localize("UTC")
    else:
        observed_at = observed_at.tz_convert("UTC")
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    observation_date = observed_at.tz_convert("Asia/Tokyo").strftime("%Y-%m-%d")
    path = (
        directory / f"{observation_date}.json"
        if daily
        else directory / f"{observed_at.strftime('%Y%m%dT%H%M%SZ')}_{run_id[:12]}.json"
    )
    payload = {
        "record_type": "forward_shadow_snapshot",
        "decision_track": result.get("decision_track", "delayed_historical"),
        "observed_at": observed_at.isoformat(),
        "observation_date_jst": observation_date,
        "observation_cadence": "daily" if daily else "point_in_time",
        "observation_status": result.get("observation_status", "evaluated"),
        "decision_observation": result.get("decision_observation", {}),
        "warning": "仮想記録です。実注文・投資助言・利益保証ではありません。",
        "manifest": manifest,
        "metrics": result.get("metrics", {}),
        "decision_cards": result.get("decision_cards", pd.DataFrame()),
        "positions": result.get("positions", pd.DataFrame()),
        "transactions": result.get("transactions", pd.DataFrame()),
        "rejected_signals": result.get("rejected_signals", pd.DataFrame()),
        "benchmark_comparisons": result.get(
            "benchmark_comparisons", pd.DataFrame()
        ),
        "account_state": {
            key: result.get(key)
            for key in (
                "account_name",
                "initial_cash",
                "cash",
                "equity",
                "realized_pnl",
                "unrealized_pnl",
                "risk_halted",
            )
        },
    }
    serialized = json.dumps(_serializable(payload), ensure_ascii=False, sort_keys=True, indent=2)
    if path.exists():
        if daily:
            existing = json.loads(path.read_text(encoding="utf-8"))
            existing_run_id = existing.get("manifest", {}).get("run_id")
            if existing_run_id == run_id:
                return path
            raise FileExistsError(
                "daily snapshot is already frozen for this account and observation date"
            )
        if path.read_text(encoding="utf-8") != serialized:
            raise FileExistsError(f"immutable snapshot already exists with different content: {path}")
        return path
    temporary = path.with_suffix(".tmp")
    temporary.write_text(serialized, encoding="utf-8")
    temporary.replace(path)
    return path
