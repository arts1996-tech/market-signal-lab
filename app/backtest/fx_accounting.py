"""Deterministic multi-currency accounting for JPY virtual accounts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from app.backtest.audit import frame_hash


FX_ACCOUNTING_VERSION = "fx-accounting-jpy-usd-v1"
FX_COLUMNS = ["price_time", "pair", "rate", "available_at", "source"]


@dataclass(frozen=True)
class FxAccountingPolicy:
    version: str = FX_ACCOUNTING_VERSION
    account_currency: str = "JPY"
    fx_spread_rate: float = 0.001
    fx_conversion_cost_rate: float = 0.0002
    missing_rate_policy: str = "reject"

    def __post_init__(self) -> None:
        if self.account_currency != "JPY":
            raise ValueError("only JPY account currency is supported")
        if self.missing_rate_policy != "reject":
            raise ValueError("only reject missing-rate policy is supported")
        if not 0 <= self.fx_spread_rate <= 0.05:
            raise ValueError("fx_spread_rate must be between 0 and 0.05")
        if not 0 <= self.fx_conversion_cost_rate <= 0.05:
            raise ValueError("fx_conversion_cost_rate must be between 0 and 0.05")


def normalize_fx_rates(rates: pd.DataFrame | None) -> pd.DataFrame:
    if rates is None or rates.empty:
        return pd.DataFrame(columns=FX_COLUMNS)
    frame = rates.copy()
    if "price_time" not in frame:
        raise ValueError("FX rates require price_time")
    if "pair" not in frame:
        if "symbol" not in frame:
            raise ValueError("FX rates require pair or symbol")
        frame["pair"] = frame["symbol"].replace({"DEXJPUS": "USDJPY"})
    if "rate" not in frame:
        if "close" not in frame:
            raise ValueError("FX rates require rate or close")
        frame["rate"] = frame["close"]
    if "available_at" not in frame:
        frame["available_at"] = frame["price_time"]
    if "source" not in frame:
        frame["source"] = "unknown"
    frame["price_time"] = pd.to_datetime(frame["price_time"], utc=True).dt.normalize()
    frame["available_at"] = pd.to_datetime(frame["available_at"], utc=True)
    frame["pair"] = frame["pair"].astype(str).str.replace("/", "", regex=False).str.upper()
    frame["rate"] = pd.to_numeric(frame["rate"], errors="coerce")
    if frame["pair"].ne("USDJPY").any():
        raise ValueError("only USDJPY FX rates are supported")
    if frame["rate"].isna().any() or frame["rate"].le(0).any():
        raise ValueError("FX rates must be positive")
    return (
        frame[FX_COLUMNS]
        .sort_values(["price_time", "available_at", "source"])
        .drop_duplicates(["price_time", "pair"], keep="last")
        .reset_index(drop=True)
    )


def fx_mid_on(rates: pd.DataFrame, currency: str, session: Any) -> float | None:
    currency = str(currency or "JPY").upper()
    if currency == "JPY":
        return 1.0
    if currency != "USD":
        return None
    point = pd.to_datetime(session, utc=True).normalize()
    matches = rates[
        (rates["pair"] == "USDJPY")
        & (rates["price_time"] == point)
        & (rates["available_at"] <= point)
    ]
    if matches.empty:
        return None
    return float(matches.iloc[-1]["rate"])


def fx_execution_rate(
    mid: float,
    *,
    side: str,
    policy: FxAccountingPolicy,
) -> float:
    if mid <= 0 or side not in {"buy", "sell"}:
        raise ValueError("valid FX mid and buy/sell side are required")
    adjustment = policy.fx_spread_rate / 2 + policy.fx_conversion_cost_rate
    return mid * (1 + adjustment if side == "buy" else 1 - adjustment)


def evaluate_fx_gate(
    prices: pd.DataFrame,
    rates: pd.DataFrame | None,
    policy: FxAccountingPolicy,
) -> dict[str, Any]:
    normalized = normalize_fx_rates(rates)
    foreign = pd.DataFrame()
    if not prices.empty and "currency" in prices:
        foreign = prices[prices["currency"].fillna("JPY").astype(str).str.upper() != "JPY"]
    warnings: list[str] = []
    unsupported = sorted(
        set(foreign.get("currency", pd.Series(dtype=str)).astype(str).str.upper())
        - {"USD"}
    )
    if unsupported:
        warnings.append("unsupported_asset_currency")
    missing: list[dict[str, str]] = []
    for row in foreign.to_dict(orient="records"):
        session = pd.to_datetime(row["price_time"], utc=True).normalize()
        currency = str(row.get("currency") or "").upper()
        if fx_mid_on(normalized, currency, session) is None:
            missing.append({"currency": currency, "session": session.date().isoformat()})
    if missing:
        warnings.append("fx_rate_missing")
    return {
        "version": policy.version,
        "status": "warning" if warnings else "verified",
        "warnings": warnings,
        "missing_rates": sorted({(item["currency"], item["session"]) for item in missing}),
        "unsupported_currencies": unsupported,
        "fx_input_hash": frame_hash(normalized),
        "rates": normalized,
    }
