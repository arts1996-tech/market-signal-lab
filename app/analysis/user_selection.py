"""Validation and immutable composition inputs for user-selected assets."""

from datetime import datetime
from hashlib import sha256
import json
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field


SelectionMarket = Literal["jp", "us"]


class TickerInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    market: SelectionMarket
    exchange: str = Field(min_length=1, max_length=64)
    symbol: str = Field(pattern=r"^[A-Z0-9.\-]{1,32}$")


class SelectionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=100)
    created_by: str = Field(min_length=1, max_length=100)
    effective_from: datetime
    rationale: str = Field(default="", max_length=1000)
    tickers: tuple[TickerInput, ...] = Field(min_length=1)


def resolve_selection_draft(draft: SelectionDraft, assets: pd.DataFrame) -> dict:
    """Resolve explicit inputs against persisted assets; never infer or add symbols."""

    required = {"asset_id", "symbol", "exchange", "asset_type"}
    if not required.issubset(assets.columns):
        raise ValueError(f"assets must contain: {', '.join(sorted(required))}")
    allowed_types = {"stock", "etf", "jp_stock", "us_stock", "jp_etf", "us_etf"}
    rows: list[dict] = []
    errors: list[dict] = []
    seen_asset_ids: set[str] = set()
    for ticker in draft.tickers:
        if (ticker.market == "jp") != (ticker.exchange.upper() == "JPX"):
            errors.append({"ticker": ticker.model_dump(), "reason": "market_exchange_mismatch"})
            continue
        matches = assets[
            (assets["symbol"].astype(str).str.upper() == ticker.symbol)
            & (assets["exchange"].astype(str).str.upper() == ticker.exchange.upper())
        ]
        if len(matches) != 1:
            errors.append({"ticker": ticker.model_dump(), "reason": "asset_not_found_or_ambiguous"})
            continue
        asset = matches.iloc[0]
        asset_type = str(asset["asset_type"])
        if asset_type not in allowed_types:
            errors.append({"ticker": ticker.model_dump(), "reason": "asset_type_not_supported"})
            continue
        asset_id = str(asset["asset_id"])
        if asset_id in seen_asset_ids:
            errors.append({"ticker": ticker.model_dump(), "reason": "duplicate_asset"})
            continue
        seen_asset_ids.add(asset_id)
        rows.append({"asset_id": asset_id, "symbol": ticker.symbol, "exchange": ticker.exchange, "market": ticker.market})
    if errors:
        return {"valid": False, "items": [], "errors": errors, "composition_hash": None}
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    return {
        "valid": True,
        "items": rows,
        "errors": [],
        "composition_hash": sha256(canonical.encode("utf-8")).hexdigest(),
    }
