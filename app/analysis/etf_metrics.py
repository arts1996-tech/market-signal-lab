"""Provider-reported ETF metadata normalization; no metric inference."""

from typing import Any

import pandas as pd


ALIASES = {
    "symbol": ["Code", "code", "symbol"],
    "observed_at": ["Date", "date", "observed_at"],
    "tracking_index": ["TrackingIndex", "trackingIndex", "tracking_index"],
    "expense_ratio": ["ExpenseRatio", "expenseRatio", "expense_ratio"],
    "net_assets": ["NetAssets", "netAssets", "net_assets"],
    "hedged": ["CurrencyHedge", "currencyHedge", "hedged"],
    "leverage_type": ["LeverageType", "leverageType", "leverage_type"],
}


def normalize_etf_metrics(rows: list[dict[str, Any]]) -> pd.DataFrame:
    normalized = []
    for row in rows:
        def value(field):
            return next((row[key] for key in ALIASES[field] if key in row), None)
        symbol, observed = value("symbol"), value("observed_at")
        if not symbol or not observed:
            continue
        try:
            observed_at = pd.Timestamp(observed, tz="UTC").to_pydatetime()
        except (TypeError, ValueError):
            continue
        expense = value("expense_ratio")
        net_assets = value("net_assets")
        normalized.append({
            "symbol": str(symbol),
            "observed_at": observed_at,
            "tracking_index": value("tracking_index"),
            "expense_ratio": float(expense) if expense not in (None, "") else None,
            "net_assets": float(net_assets) if net_assets not in (None, "") else None,
            "hedged": value("hedged"),
            "leverage_type": value("leverage_type"),
        })
    return pd.DataFrame(normalized)
