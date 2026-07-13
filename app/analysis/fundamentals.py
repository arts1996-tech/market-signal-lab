"""Validation and normalization for provider-reported phase 3 fundamentals."""

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
    return result if pd.notna(result) else None


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
