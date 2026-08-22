"""Point-in-time asset-universe and delisting safeguards."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from app.backtest.audit import frame_hash


ASSET_LIFECYCLE_VERSION = "asset-lifecycle-conservative-v1"
INVESTABLE = "investable"
NON_INVESTABLE = "non_investable"
SUSPENDED = "suspended"
DELISTED = "delisted"
UNKNOWN = "unknown"
VALID_STATUSES = {INVESTABLE, NON_INVESTABLE, SUSPENDED, DELISTED, UNKNOWN}

LIFECYCLE_COLUMNS = [
    "symbol",
    "effective_from",
    "effective_to",
    "listed_on",
    "delisted_on",
    "market",
    "sector_17",
    "sector_33",
    "investability_status",
    "source",
    "available_at",
    "fetched_at",
]
UNIVERSE_COVERAGE_COLUMNS = [
    "period_start",
    "period_end",
    "status",
    "source",
    "observed_asset_count",
    "input_hash",
    "available_at",
    "checked_at",
]


@dataclass(frozen=True)
class AssetLifecyclePolicy:
    version: str = ASSET_LIFECYCLE_VERSION
    missing_coverage_policy: str = "warn"
    delisting_recovery_policy: str = "zero_recovery"
    absent_from_complete_universe_policy: str = "zero_recovery"

    def __post_init__(self) -> None:
        if self.missing_coverage_policy not in {"warn", "reject"}:
            raise ValueError("missing_coverage_policy must be warn or reject")
        if self.delisting_recovery_policy != "zero_recovery":
            raise ValueError("only zero_recovery delisting policy is supported")
        if self.absent_from_complete_universe_policy != "zero_recovery":
            raise ValueError("only zero_recovery absent-universe policy is supported")


def _utc_timestamp(
    value: Any,
    *,
    field: str,
    required: bool = False,
    normalize: bool = False,
) -> pd.Timestamp | None:
    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
        if required:
            raise ValueError(f"{field} is required")
        return None
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"{field} must be a valid timestamp")
    timestamp = pd.Timestamp(parsed)
    return timestamp.normalize() if normalize else timestamp


def normalize_asset_lifecycle(records: pd.DataFrame | None) -> pd.DataFrame:
    if records is None or records.empty:
        return pd.DataFrame(columns=LIFECYCLE_COLUMNS)
    required = {"symbol", "effective_from", "investability_status"}
    missing = required.difference(records.columns)
    if missing:
        raise ValueError(f"asset lifecycle missing columns: {sorted(missing)}")
    rows: list[dict[str, Any]] = []
    for raw in records.to_dict(orient="records"):
        symbol = str(raw.get("symbol") or "").strip().upper()
        if not symbol:
            raise ValueError("asset lifecycle symbol is required")
        effective_from = _utc_timestamp(
            raw.get("effective_from"), field="effective_from", required=True, normalize=True
        )
        effective_to = _utc_timestamp(
            raw.get("effective_to"), field="effective_to", normalize=True
        )
        listed_on = _utc_timestamp(raw.get("listed_on"), field="listed_on", normalize=True)
        delisted_on = _utc_timestamp(
            raw.get("delisted_on"), field="delisted_on", normalize=True
        )
        if effective_to is not None and effective_from > effective_to:
            raise ValueError("asset lifecycle effective_from must not exceed effective_to")
        if listed_on is not None and delisted_on is not None and listed_on > delisted_on:
            raise ValueError("listed_on must not exceed delisted_on")
        status = str(raw.get("investability_status") or "").strip().lower()
        if status not in VALID_STATUSES:
            raise ValueError("asset lifecycle investability_status is invalid")
        rows.append(
            {
                "symbol": symbol,
                "effective_from": effective_from,
                "effective_to": effective_to,
                "listed_on": listed_on,
                "delisted_on": delisted_on,
                "market": str(raw.get("market") or "").strip() or None,
                "sector_17": str(raw.get("sector_17") or "").strip() or None,
                "sector_33": str(raw.get("sector_33") or "").strip() or None,
                "investability_status": status,
                "source": str(raw.get("source") or "unknown"),
                "available_at": _utc_timestamp(
                    raw.get("available_at"), field="available_at", required=True
                ),
                "fetched_at": _utc_timestamp(raw.get("fetched_at"), field="fetched_at"),
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["effective_from", "symbol", "available_at"]
    ).reset_index(drop=True)


def normalize_asset_universe_coverage(coverage: pd.DataFrame | None) -> pd.DataFrame:
    if coverage is None or coverage.empty:
        return pd.DataFrame(columns=UNIVERSE_COVERAGE_COLUMNS)
    required = {"period_start", "period_end", "status", "source"}
    missing = required.difference(coverage.columns)
    if missing:
        raise ValueError(f"asset universe coverage missing columns: {sorted(missing)}")
    rows: list[dict[str, Any]] = []
    for raw in coverage.to_dict(orient="records"):
        start = _utc_timestamp(
            raw.get("period_start"), field="period_start", required=True, normalize=True
        )
        end = _utc_timestamp(
            raw.get("period_end"), field="period_end", required=True, normalize=True
        )
        if start > end:
            raise ValueError("asset universe coverage start must not exceed end")
        status = str(raw.get("status") or "").strip().lower()
        if status not in {"complete", "partial", "unavailable"}:
            raise ValueError("asset universe coverage status is invalid")
        count = raw.get("observed_asset_count")
        count = None if count is None or pd.isna(count) else int(count)
        if count is not None and count < 0:
            raise ValueError("observed_asset_count must be non-negative")
        rows.append(
            {
                "period_start": start,
                "period_end": end,
                "status": status,
                "source": str(raw.get("source") or "unknown"),
                "observed_asset_count": count,
                "input_hash": str(raw.get("input_hash") or "") or None,
                "available_at": _utc_timestamp(
                    raw.get("available_at"), field="available_at", required=True
                ),
                "checked_at": _utc_timestamp(raw.get("checked_at"), field="checked_at"),
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["period_start", "source", "available_at"]
    ).reset_index(drop=True)


def _coverage_on(coverage: pd.DataFrame, session: pd.Timestamp) -> pd.DataFrame:
    if coverage.empty:
        return coverage
    return coverage[
        (coverage["status"] == "complete")
        & (coverage["period_start"] <= session)
        & (coverage["period_end"] >= session)
        & (coverage["available_at"] <= session)
    ]


def lifecycle_record_on(
    records: pd.DataFrame,
    coverage: pd.DataFrame,
    *,
    symbol: str,
    session: Any,
) -> tuple[dict[str, Any] | None, bool]:
    point = _utc_timestamp(session, field="session", required=True, normalize=True)
    covered = _coverage_on(coverage, point)
    if covered.empty:
        return None, False
    sources = set(covered["source"].astype(str))
    matches = records[
        (records["symbol"] == str(symbol).strip().upper())
        & records["source"].astype(str).isin(sources)
        & (records["effective_from"] <= point)
        & (records["effective_to"].isna() | (records["effective_to"] >= point))
        & (records["available_at"] <= point)
    ]
    if matches.empty:
        return None, True
    selected = matches.sort_values(
        ["effective_from", "available_at", "fetched_at"], na_position="first"
    ).iloc[-1]
    return selected.to_dict(), True


def investable_universe_as_of(
    records: pd.DataFrame | None,
    coverage: pd.DataFrame | None,
    as_of: Any,
) -> dict[str, Any]:
    normalized_records = normalize_asset_lifecycle(records)
    normalized_coverage = normalize_asset_universe_coverage(coverage)
    point = _utc_timestamp(as_of, field="as_of", required=True, normalize=True)
    covered = _coverage_on(normalized_coverage, point)
    if covered.empty:
        return {"as_of": point, "coverage_status": "unverified", "symbols": [], "records": pd.DataFrame()}
    sources = set(covered["source"].astype(str))
    candidates = normalized_records[
        normalized_records["source"].astype(str).isin(sources)
        & (normalized_records["effective_from"] <= point)
        & (
            normalized_records["effective_to"].isna()
            | (normalized_records["effective_to"] >= point)
        )
        & (normalized_records["available_at"] <= point)
    ].copy()
    if candidates.empty:
        return {"as_of": point, "coverage_status": "complete", "symbols": [], "records": candidates}
    candidates = candidates.sort_values(
        ["symbol", "effective_from", "available_at", "fetched_at"],
        na_position="first",
    ).drop_duplicates("symbol", keep="last")
    investable = candidates[
        (candidates["investability_status"] == INVESTABLE)
        & (candidates["listed_on"].isna() | (candidates["listed_on"] <= point))
        & (candidates["delisted_on"].isna() | (candidates["delisted_on"] > point))
    ]
    return {
        "as_of": point,
        "coverage_status": "complete",
        "symbols": sorted(investable["symbol"].tolist()),
        "records": candidates.reset_index(drop=True),
    }


def evaluate_asset_lifecycle_gate(
    prices: pd.DataFrame,
    records: pd.DataFrame | None,
    coverage: pd.DataFrame | None,
    policy: AssetLifecyclePolicy,
) -> dict[str, Any]:
    lifecycle_enabled = records is not None or coverage is not None
    normalized_records = normalize_asset_lifecycle(records)
    normalized_coverage = normalize_asset_universe_coverage(coverage)
    unverified_sessions: list[str] = []
    if lifecycle_enabled and not prices.empty and "price_time" in prices:
        sessions = pd.to_datetime(prices["price_time"], utc=True).dt.normalize().unique()
        for session in sorted(pd.Timestamp(value) for value in sessions):
            if _coverage_on(normalized_coverage, session).empty:
                unverified_sessions.append(session.date().isoformat())
    warnings = ["asset_universe_coverage_unverified"] if unverified_sessions else []
    return {
        "version": policy.version,
        "status": "warning" if warnings else "verified",
        "warnings": warnings,
        "unverified_sessions": unverified_sessions,
        "record_count": len(normalized_records),
        "coverage_count": len(normalized_coverage),
        "lifecycle_input_hash": frame_hash(normalized_records),
        "universe_coverage_input_hash": frame_hash(normalized_coverage),
        "records": normalized_records,
        "coverage": normalized_coverage,
    }
