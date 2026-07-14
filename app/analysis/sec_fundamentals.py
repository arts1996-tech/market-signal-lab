"""Normalize SEC Company Facts JSON without inferring missing fundamentals."""

from collections import defaultdict
from typing import Any

import pandas as pd


SEC_TAGS = {
    "sales": ("RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "Revenues"),
    "operating_profit": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss",),
    "eps": ("EarningsPerShareDiluted", "EarningsPerShareBasic"),
    "equity": ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    "total_assets": ("Assets",),
    "operating_cashflow": ("NetCashProvidedByUsedInOperatingActivities",),
    "book_value_per_share": ("BookValuePerShare",),
}


def _timestamp(value: Any) -> pd.Timestamp | None:
    if not value:
        return None
    try:
        return pd.Timestamp(value, tz="UTC")
    except (TypeError, ValueError):
        return None


def _facts_for_tag(facts: dict[str, Any], tags: tuple[str, ...]) -> list[dict[str, Any]]:
    for tag in tags:
        units = facts.get(tag, {}).get("units", {})
        for unit_rows in units.values():
            if unit_rows:
                return unit_rows
    return []


def normalize_sec_companyfacts(payload: dict[str, Any], symbol: str | None = None) -> pd.DataFrame:
    """Return filing-timed rows compatible with the phase-3 fundamentals model.

    Only facts explicitly present in the SEC payload are copied. Values from
    different filings are not combined unless their `filed` and `end` dates
    match, preventing an implicit restatement or period join.
    """
    facts = (payload.get("facts") or {}).get("us-gaap") or {}
    grouped: dict[tuple[pd.Timestamp, str], dict[str, Any]] = defaultdict(dict)
    for field, tags in SEC_TAGS.items():
        for fact in _facts_for_tag(facts, tags):
            filed = _timestamp(fact.get("filed"))
            period_end = fact.get("end")
            if filed is None or not period_end or fact.get("val") is None:
                continue
            key = (filed, str(period_end))
            try:
                grouped[key][field] = float(fact["val"])
            except (TypeError, ValueError):
                continue
    rows = []
    for (filed, period_end), values in sorted(grouped.items()):
        rows.append({
            "symbol": symbol or payload.get("entityName") or payload.get("cik"),
            "disclosed_at": filed.to_pydatetime(),
            "period_end": pd.Timestamp(period_end).date(),
            "source": "sec_companyfacts",
            **values,
        })
    return pd.DataFrame(rows)
