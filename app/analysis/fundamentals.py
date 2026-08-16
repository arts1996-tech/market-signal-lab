"""Validation and normalization for provider-reported phase 3 fundamentals."""

import math
from datetime import datetime
from typing import Any

import pandas as pd


FIELD_ALIASES = {
    "symbol": ["Code", "code", "LocalCode", "local_code"],
    "disclosed_at": ["DisclosedDate", "disclosedDate", "DiscDate", "disclosed_at"],
    "period_end": ["CurrentPeriodEndDate", "currentPeriodEndDate", "CurPerEn", "period_end"],
    "sales": ["Sales", "sales", "Revenue", "revenue"],
    "operating_profit": ["OperatingProfit", "operatingProfit", "OP", "operating_profit"],
    "net_income": ["NetIncome", "netIncome", "NP", "net_income"],
    "eps": ["EarningsPerShare", "earningsPerShare", "eps"],
    "book_value_per_share": ["BookValuePerShare", "BPS", "book_value_per_share"],
    "equity": ["Equity", "Eq", "equity"],
    "total_assets": ["TotalAssets", "TA", "totalAssets", "total_assets"],
    "operating_cashflow": ["CashFlowsFromUsedInOperatingActivities", "CFO", "operating_cashflow"],
}


def _value(row: dict[str, Any], aliases: list[str]) -> Any:
    for key in aliases:
        if key in row:
            return row[key]
    return None


def _number(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if pd.notna(result) and math.isfinite(result) else None


def provider_reported_fundamental_details(details: Any) -> dict[str, Any]:
    """Expose only explicitly persisted, display-safe provider details.

    Missing currency, unit, and book value per share remain ``None``. In
    particular, book value per share is never reconstructed from total equity.
    """
    if not isinstance(details, dict):
        details = {}

    def text_value(key: str) -> str | None:
        value = details.get(key)
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    return {
        "book_value_per_share": _number(details.get("book_value_per_share")),
        "currency": text_value("currency"),
        "unit": text_value("unit"),
    }


def normalize_financial_summary(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Normalize known fields and reject rows without symbol/disclosure timing."""
    normalized = []
    for row in rows:
        symbol = _value(row, FIELD_ALIASES["symbol"])
        disclosed_at = _value(row, FIELD_ALIASES["disclosed_at"])
        if not symbol or not disclosed_at:
            continue
        try:
            disclosed = pd.Timestamp(disclosed_at, tz="UTC")
        except (TypeError, ValueError):
            continue
        record = {"symbol": str(symbol), "disclosed_at": disclosed.to_pydatetime()}
        period_end = _value(row, FIELD_ALIASES["period_end"])
        record["period_end"] = pd.Timestamp(period_end).date() if period_end else None
        for field, aliases in FIELD_ALIASES.items():
            if field not in {"symbol", "disclosed_at", "period_end"}:
                record[field] = _number(_value(row, aliases))
        normalized.append(record)
    return pd.DataFrame(normalized)


def fundamentals_as_of(snapshot: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """Keep only disclosures available at the historical analysis timestamp."""
    if snapshot.empty:
        return snapshot.copy()
    cutoff = pd.Timestamp(as_of)
    cutoff = cutoff.tz_localize("UTC") if cutoff.tzinfo is None else cutoff.tz_convert("UTC")
    frame = snapshot.copy()
    frame["disclosed_at"] = pd.to_datetime(frame["disclosed_at"], utc=True)
    return frame[frame["disclosed_at"] <= cutoff].sort_values("disclosed_at")


def derive_fundamental_metrics(snapshot: dict[str, Any], price: float | None = None) -> dict[str, float | None]:
    """Calculate ratios only from provider-reported values and an observed price."""
    def ratio(numerator, denominator):
        if numerator is None or denominator in (None, 0) or pd.isna(numerator) or pd.isna(denominator):
            return None
        return float(numerator) / float(denominator)

    eps = snapshot.get("eps")
    equity = snapshot.get("equity")
    net_income = snapshot.get("net_income")
    sales = snapshot.get("sales")
    return {
        "per": ratio(price, eps),
        # PBR needs book value per share; do not approximate it from total equity.
        "pbr": ratio(price, snapshot.get("book_value_per_share")),
        "roe": ratio(net_income, equity),
        "operating_margin": ratio(snapshot.get("operating_profit"), sales),
    }
