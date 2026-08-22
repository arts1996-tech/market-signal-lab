"""Point-in-time corporate-action model and conservative coverage gate."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import pandas as pd

from app.backtest.audit import frame_hash


CORPORATE_ACTION_VERSION = "corporate-actions-conservative-v1"
STOCK_SPLIT = "stock_split"
REVERSE_SPLIT = "reverse_split"
CASH_DIVIDEND = "cash_dividend"
MERGER = "merger"
SHARE_EXCHANGE = "share_exchange"
SUPPORTED_ACTIONS = {STOCK_SPLIT, REVERSE_SPLIT, CASH_DIVIDEND}
UNSUPPORTED_ACTIONS = {MERGER, SHARE_EXCHANGE}
KNOWN_ACTIONS = SUPPORTED_ACTIONS | UNSUPPORTED_ACTIONS

ACTION_COLUMNS = [
    "action_id",
    "symbol",
    "action_type",
    "announced_at",
    "effective_date",
    "ex_date",
    "record_date",
    "payable_date",
    "ratio",
    "cash_per_share",
    "currency",
    "source",
    "status",
    "fetched_at",
]
COVERAGE_COLUMNS = [
    "symbol",
    "period_start",
    "period_end",
    "status",
    "source",
    "checked_at",
]


@dataclass(frozen=True)
class CorporateActionPolicy:
    version: str = CORPORATE_ACTION_VERSION
    missing_coverage_policy: str = "warn"
    unsupported_event_policy: str = "reject_or_defer"
    fractional_share_policy: str = "defer"
    dividend_tax_rate: float = 0.0
    account_currency: str = "JPY"

    def __post_init__(self) -> None:
        if self.missing_coverage_policy not in {"warn", "reject"}:
            raise ValueError("missing_coverage_policy must be warn or reject")
        if self.unsupported_event_policy != "reject_or_defer":
            raise ValueError("only reject_or_defer unsupported-event policy is supported")
        if self.fractional_share_policy != "defer":
            raise ValueError("only defer fractional-share policy is supported")
        if not 0 <= self.dividend_tax_rate <= 1:
            raise ValueError("dividend_tax_rate must be between 0 and 1")
        if self.dividend_tax_rate != 0:
            raise ValueError(
                "dividend_tax_rate must remain 0; virtual-account results are pretax"
            )
        if not self.account_currency:
            raise ValueError("account_currency is required")


def _utc_timestamp(
    value: Any,
    *,
    field: str,
    required: bool = False,
    normalize: bool = True,
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


def _positive_number(value: Any, *, field: str, allow_zero: bool = False) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    minimum_ok = parsed >= 0 if allow_zero else parsed > 0
    if not math.isfinite(parsed) or not minimum_ok:
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{field} must be {qualifier}")
    return parsed


def normalize_corporate_actions(actions: pd.DataFrame | None) -> pd.DataFrame:
    """Validate provider-reported events without inferring missing terms."""
    if actions is None or actions.empty:
        return pd.DataFrame(columns=ACTION_COLUMNS + ["event_date"])
    required = {"symbol", "action_type"}
    missing = required.difference(actions.columns)
    if missing:
        raise ValueError(f"corporate actions missing columns: {sorted(missing)}")
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(actions.to_dict(orient="records")):
        symbol = str(raw.get("symbol") or "").strip().upper()
        action_type = str(raw.get("action_type") or "").strip().lower()
        if not symbol:
            raise ValueError("corporate action symbol is required")
        if action_type not in KNOWN_ACTIONS:
            raise ValueError(f"unknown corporate action type: {action_type}")
        announced_at = _utc_timestamp(
            raw.get("announced_at"), field="announced_at", normalize=False
        )
        effective_date = _utc_timestamp(
            raw.get("effective_date"), field="effective_date"
        )
        ex_date = _utc_timestamp(raw.get("ex_date"), field="ex_date")
        record_date = _utc_timestamp(raw.get("record_date"), field="record_date")
        payable_date = _utc_timestamp(raw.get("payable_date"), field="payable_date")
        event_date = ex_date if action_type == CASH_DIVIDEND else effective_date or ex_date
        if event_date is None:
            raise ValueError("effective_date or ex_date is required")
        if announced_at is not None and announced_at.normalize() > event_date:
            raise ValueError("announced_at must not be after the event date")
        if effective_date is None:
            effective_date = event_date
        ratio = None
        cash_per_share = None
        currency = str(raw.get("currency") or "").strip().upper() or None
        if action_type in {STOCK_SPLIT, REVERSE_SPLIT}:
            ratio = _positive_number(raw.get("ratio"), field="ratio")
            if action_type == STOCK_SPLIT and ratio <= 1:
                raise ValueError("stock_split ratio must be greater than 1")
            if action_type == REVERSE_SPLIT and ratio >= 1:
                raise ValueError("reverse_split ratio must be less than 1")
        elif action_type == CASH_DIVIDEND:
            cash_per_share = _positive_number(
                raw.get("cash_per_share"), field="cash_per_share", allow_zero=True
            )
            if ex_date is None or record_date is None or payable_date is None:
                raise ValueError(
                    "cash dividend requires ex_date, record_date, and payable_date"
                )
            if not (ex_date <= record_date <= payable_date):
                raise ValueError("cash dividend dates must be ex <= record <= payable")
            if currency is None:
                raise ValueError("cash dividend currency is required")
        action_id = str(raw.get("action_id") or f"row-{index}")
        status = str(raw.get("status") or "confirmed").strip().lower()
        if status not in {"confirmed", "pending", "cancelled"}:
            raise ValueError("corporate action status is invalid")
        rows.append(
            {
                "action_id": action_id,
                "symbol": symbol,
                "action_type": action_type,
                "announced_at": announced_at,
                "effective_date": effective_date,
                "ex_date": ex_date,
                "record_date": record_date,
                "payable_date": payable_date,
                "event_date": event_date,
                "ratio": ratio,
                "cash_per_share": cash_per_share,
                "currency": currency,
                "source": str(raw.get("source") or "unknown"),
                "status": status,
                "fetched_at": _utc_timestamp(
                    raw.get("fetched_at"), field="fetched_at", normalize=False
                ),
            }
        )
    frame = pd.DataFrame(rows)
    duplicate_keys = frame.duplicated(["source", "action_id"], keep=False)
    if duplicate_keys.any():
        raise ValueError("corporate action source/action_id must be unique")
    return frame.sort_values(["event_date", "symbol", "action_id"]).reset_index(drop=True)


def normalize_corporate_action_coverage(coverage: pd.DataFrame | None) -> pd.DataFrame:
    if coverage is None or coverage.empty:
        return pd.DataFrame(columns=COVERAGE_COLUMNS)
    required = {"symbol", "period_start", "period_end", "status"}
    missing = required.difference(coverage.columns)
    if missing:
        raise ValueError(f"corporate action coverage missing columns: {sorted(missing)}")
    rows = []
    for raw in coverage.to_dict(orient="records"):
        start = _utc_timestamp(raw.get("period_start"), field="period_start", required=True)
        end = _utc_timestamp(raw.get("period_end"), field="period_end", required=True)
        if start > end:
            raise ValueError("corporate action coverage start must not exceed end")
        status = str(raw.get("status") or "").strip().lower()
        if status not in {"complete", "partial", "unavailable"}:
            raise ValueError("corporate action coverage status is invalid")
        rows.append(
            {
                "symbol": str(raw.get("symbol") or "").strip().upper(),
                "period_start": start,
                "period_end": end,
                "status": status,
                "source": str(raw.get("source") or "unknown"),
                "checked_at": _utc_timestamp(
                    raw.get("checked_at"), field="checked_at", normalize=False
                ),
            }
        )
    if any(not row["symbol"] for row in rows):
        raise ValueError("corporate action coverage symbol is required")
    return pd.DataFrame(rows).sort_values(["symbol", "period_start"]).reset_index(drop=True)


def _fully_covered(
    coverage: pd.DataFrame,
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> bool:
    rows = coverage[
        (coverage["symbol"] == symbol) & (coverage["status"] == "complete")
    ].sort_values("period_start")
    cursor = start
    for row in rows.itertuples(index=False):
        if row.period_end < cursor:
            continue
        if row.period_start > cursor:
            return False
        cursor = max(cursor, row.period_end + pd.Timedelta(days=1))
        if cursor > end:
            return True
    return cursor > end


def evaluate_corporate_action_gate(
    prices: pd.DataFrame,
    actions: pd.DataFrame | None,
    coverage: pd.DataFrame | None,
    policy: CorporateActionPolicy,
) -> dict[str, Any]:
    normalized_actions = normalize_corporate_actions(actions)
    normalized_coverage = normalize_corporate_action_coverage(coverage)
    unverified_symbols: list[str] = []
    if not prices.empty:
        price_frame = prices.copy()
        price_frame["price_time"] = pd.to_datetime(
            price_frame["price_time"], utc=True
        ).dt.normalize()
        price_ranges = price_frame.groupby("symbol")["price_time"].agg(
            period_start="min", period_end="max"
        )
        for symbol, period in price_ranges.iterrows():
            if not _fully_covered(
                normalized_coverage,
                str(symbol),
                period["period_start"],
                period["period_end"],
            ):
                unverified_symbols.append(str(symbol))
    warnings = []
    if unverified_symbols:
        warnings.append("corporate_action_coverage_unverified")
    unsupported = normalized_actions[
        normalized_actions["action_type"].isin(UNSUPPORTED_ACTIONS)
        & normalized_actions["status"].ne("cancelled")
    ]
    if not unsupported.empty:
        warnings.append("unsupported_corporate_action_present")
    if normalized_actions["status"].eq("pending").any():
        warnings.append("unconfirmed_corporate_action_present")
    return {
        "version": policy.version,
        "status": "warning" if warnings else "verified",
        "warnings": warnings,
        "unverified_symbols": sorted(unverified_symbols),
        "event_count": len(normalized_actions),
        "unsupported_event_count": len(unsupported),
        "action_input_hash": frame_hash(normalized_actions),
        "coverage_input_hash": frame_hash(normalized_coverage),
        "actions": normalized_actions,
        "coverage": normalized_coverage,
    }


def events_on(actions: pd.DataFrame, session: pd.Timestamp) -> list[dict[str, Any]]:
    if actions.empty:
        return []
    return actions[
        (actions["event_date"] == session) & actions["status"].ne("cancelled")
    ].to_dict(orient="records")


def known_unsupported_event_in_horizon(
    actions: pd.DataFrame,
    *,
    symbol: str,
    signal_date: pd.Timestamp,
    entry_date: pd.Timestamp,
    horizon_end: pd.Timestamp,
) -> dict[str, Any] | None:
    if actions.empty:
        return None
    matches = actions[
        (actions["symbol"] == symbol)
        & (actions["action_type"].isin(UNSUPPORTED_ACTIONS))
        & (actions["event_date"] >= entry_date)
        & (actions["event_date"] <= horizon_end)
        & actions["status"].ne("cancelled")
        & actions["announced_at"].notna()
        & (actions["announced_at"] <= signal_date)
    ]
    return None if matches.empty else matches.iloc[0].to_dict()
