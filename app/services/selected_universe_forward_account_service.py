"""Append-only cash accounts restricted to one immutable user selection version."""

from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis.decision_tracks import DECISION_TRACK_DELAYED
from app.backtest.audit import stable_payload_hash
from app.backtest.forward_account import ForwardAccountRule, advance_forward_accounts_as_of
from app.backtest.ohlc import MarketImpactAssumptions, PortfolioRiskRules
from app.backtest.portfolio import ExecutionAssumptions
from app.database.models import Asset, UserAssetSelection, UserAssetSelectionItem
from app.services.forward_account_ledger import (
    load_latest_forward_account_states,
    persist_forward_accounts,
)


SELECTED_ACCOUNT_SCOPE = "selected_universe"
SELECTED_ACCOUNT_CHANGE_POLICY = "new_entries_forbidden_existing_positions_follow_exit_v1"
SELECTED_ACCOUNT_STRATEGY_VERSION = "selected-universe-forward-cash-v1"


def _utc_timestamp(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def selected_account_rules(selection_id: str) -> tuple[ForwardAccountRule, ...]:
    """Return stable, isolated account identities for one immutable selection row."""

    identity = selection_id.replace("-", "")
    if len(identity) != 32 or not all(
        character in "0123456789abcdefABCDEF" for character in identity
    ):
        raise ValueError("selection_id must be a UUID")
    prefix = f"sel_{identity.lower()}"
    return (
        ForwardAccountRule(
            account_name=f"{prefix}_short",
            label="指定集合・短期",
            strategy_version=SELECTED_ACCOUNT_STRATEGY_VERSION,
            stop_loss=-0.05,
            take_profit=0.08,
            maximum_holding_days=10,
        ),
        ForwardAccountRule(
            account_name=f"{prefix}_mid",
            label="指定集合・中期",
            strategy_version=SELECTED_ACCOUNT_STRATEGY_VERSION,
            stop_loss=-0.10,
            take_profit=0.18,
            maximum_holding_days=60,
        ),
    )


def enforce_selected_cash_universe(
    signals: pd.DataFrame,
    selected_assets: list[dict],
    *,
    allow_new_entries: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reject selection-external, non-JPX/JPY and non-cash-long entry signals."""

    if signals.empty:
        return signals.copy(), pd.DataFrame()
    required = {"symbol", "side"}
    missing = required.difference(signals.columns)
    if missing:
        raise ValueError(f"signals missing required columns: {sorted(missing)}")
    by_symbol = {str(asset["symbol"]): asset for asset in selected_assets}
    eligible_rows: list[dict] = []
    rejected_rows: list[dict] = []
    for row in signals.to_dict(orient="records"):
        symbol = str(row["symbol"])
        asset = by_symbol.get(symbol)
        reasons: list[str] = []
        if not allow_new_entries:
            reasons.append("superseded_selection_version_new_entries_forbidden")
        if asset is None:
            reasons.append("outside_allowed_selection")
        else:
            if asset["exchange"] != "JPX" or asset["currency"] != "JPY":
                reasons.append("cross_market_forward_account_not_yet_supported")
            if asset["item_status"] != "active":
                reasons.append("selection_item_inactive")
        if str(row["side"]) != "long":
            reasons.append("cash_long_only")
        if reasons:
            rejected_rows.append(
                {
                    **row,
                    "status": "selected_universe_entry_rejected",
                    "reason_codes": sorted(set(reasons)),
                }
            )
        else:
            eligible_rows.append(row)
    return pd.DataFrame(eligible_rows, columns=signals.columns), pd.DataFrame(rejected_rows)


def _selected_assets(session: Session, selection_id: str) -> list[dict]:
    rows = session.execute(
        select(UserAssetSelectionItem, Asset)
        .join(Asset, Asset.id == UserAssetSelectionItem.asset_id)
        .where(UserAssetSelectionItem.selection_id == selection_id)
        .order_by(UserAssetSelectionItem.display_order)
    ).all()
    return [
        {
            "asset_id": item.asset_id,
            "symbol": asset.symbol,
            "exchange": asset.exchange or "",
            "currency": asset.currency,
            "item_status": item.status,
        }
        for item, asset in rows
    ]


def advance_selected_universe_forward_accounts(
    session: Session,
    *,
    selection_id: str,
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    observation: dict,
    decisions: pd.DataFrame | None = None,
    assumptions: ExecutionAssumptions | None = None,
    market_impact: MarketImpactAssumptions | None = None,
    risk_rules: PortfolioRiskRules | None = None,
) -> dict:
    """Advance two delayed-research accounts without touching standard accounts."""

    if observation.get("decision_track") != DECISION_TRACK_DELAYED:
        raise ValueError(
            "selected-universe forward accounts are delayed_historical research only"
        )
    selection = session.get(UserAssetSelection, selection_id)
    if selection is None:
        raise ValueError("user asset selection does not exist")
    if selection.status != "active":
        raise ValueError("inactive user asset selection cannot start or advance an account")
    observed_at = _utc_timestamp(observation["observed_at"])
    if _utc_timestamp(selection.effective_from) > observed_at:
        raise ValueError("selection effective_from is after the account observation")
    assets = _selected_assets(session, selection.id)
    if not assets:
        raise ValueError("user asset selection has no items")

    latest_version = session.scalar(
        select(func.max(UserAssetSelection.version)).where(
            UserAssetSelection.selection_key == selection.selection_key
        )
    )
    allow_new_entries = selection.version == latest_version
    eligible_signals, boundary_rejections = enforce_selected_cash_universe(
        signals,
        assets,
        allow_new_entries=allow_new_entries,
    )
    supported_symbols = {
        asset["symbol"]
        for asset in assets
        if asset["item_status"] == "active"
        and asset["exchange"] == "JPX"
        and asset["currency"] == "JPY"
    }
    selected_prices = (
        prices[prices["symbol"].astype(str).isin(supported_symbols)].copy()
        if not prices.empty and "symbol" in prices
        else prices.iloc[0:0].copy()
    )
    selected_decisions = decisions
    if decisions is not None and not decisions.empty and "symbol" in decisions:
        selected_decisions = decisions[
            decisions["symbol"].astype(str).isin(supported_symbols)
        ].copy()

    rules = selected_account_rules(selection.id)
    account_names = {rule.account_name for rule in rules}
    previous_states = load_latest_forward_account_states(
        session,
        DECISION_TRACK_DELAYED,
        account_names=account_names,
    )
    result = advance_forward_accounts_as_of(
        eligible_signals,
        selected_prices,
        as_of=observed_at,
        previous_states=previous_states or None,
        assumptions=assumptions,
        market_impact=market_impact,
        risk_rules=risk_rules,
        account_rules=rules,
    )
    selection_metadata = {
        "account_scope": SELECTED_ACCOUNT_SCOPE,
        "allowed_selection_id": selection.id,
        "allowed_selection_version": selection.version,
        "allowed_selection_composition_hash": selection.composition_hash,
        "selection_change_policy": SELECTED_ACCOUNT_CHANGE_POLICY,
    }
    rejection_records = (
        [] if boundary_rejections.empty else boundary_rejections.to_dict(orient="records")
    )
    for account in result["accounts"].values():
        account.update(selection_metadata)
        existing_rejections = account.get("rejected_signals")
        if isinstance(existing_rejections, pd.DataFrame):
            existing_records = existing_rejections.to_dict(orient="records")
        else:
            existing_records = list(existing_rejections or [])
        account["rejected_signals"] = pd.DataFrame(
            [*existing_records, *rejection_records]
        )

    scoped_observation = {
        **observation,
        "input_hash": stable_payload_hash(
            {
                "observation_input_hash": observation.get("input_hash"),
                "selection_id": selection.id,
                "selection_version": selection.version,
                "composition_hash": selection.composition_hash,
            }
        ),
    }
    result["account_scope"] = SELECTED_ACCOUNT_SCOPE
    result["allowed_selection"] = {
        "selection_id": selection.id,
        "selection_key": selection.selection_key,
        "selection_version": selection.version,
        "composition_hash": selection.composition_hash,
        "symbols": [asset["symbol"] for asset in assets if asset["item_status"] == "active"],
        "new_entries_allowed": allow_new_entries,
    }
    result["boundary_rejections"] = boundary_rejections
    result["ledger"] = persist_forward_accounts(
        session,
        result,
        observation=scoped_observation,
        decisions=selected_decisions,
    )
    return result
