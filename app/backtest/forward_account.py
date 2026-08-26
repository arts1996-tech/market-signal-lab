from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from app.analysis.signal_generation import known_prices_as_of
from app.backtest.audit import stable_payload_hash
from app.backtest.corporate_actions import CorporateActionPolicy
from app.backtest.asset_lifecycle import AssetLifecyclePolicy
from app.backtest.fx_accounting import FxAccountingPolicy
from app.backtest.ohlc import (
    MarketImpactAssumptions,
    PortfolioRiskRules,
    simulate_ohlc_portfolio,
)
from app.backtest.portfolio import ExecutionAssumptions
from app.backtest.tax_accounting import TaxAccountingPolicy


FORWARD_ACCOUNT_STATE_VERSION = "forward-account-state-v1"
FORWARD_EXECUTION_VERSION = "ohlc-next-open-conservative-v6"


@dataclass(frozen=True)
class ForwardAccountRule:
    account_name: str
    label: str
    strategy_version: str
    stop_loss: float
    take_profit: float
    maximum_holding_days: int
    initial_cash: float = 2_500_000.0

    def __post_init__(self) -> None:
        if self.initial_cash != 2_500_000.0:
            raise ValueError("forward account initial cash must be JPY 2,500,000")
        if self.stop_loss >= 0 or self.take_profit <= 0:
            raise ValueError("stop_loss must be negative and take_profit must be positive")
        if self.maximum_holding_days <= 0:
            raise ValueError("maximum_holding_days must be positive")


FORWARD_ACCOUNT_RULES = (
    ForwardAccountRule(
        account_name="short_term",
        label="短期",
        strategy_version="forward-short-term-v1",
        stop_loss=-0.05,
        take_profit=0.08,
        maximum_holding_days=10,
    ),
    ForwardAccountRule(
        account_name="mid_term",
        label="中期",
        strategy_version="forward-mid-term-v1",
        stop_loss=-0.10,
        take_profit=0.18,
        maximum_holding_days=60,
    ),
)


def _utc_timestamp(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _empty_signal_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "signal_date",
            "entry_date",
            "symbol",
            "side",
            "score",
            "reasons",
        ]
    )


def _normalize_signals(signals: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    if signals.empty:
        return _empty_signal_frame()
    required = {"signal_date", "entry_date", "symbol", "side"}
    missing = required.difference(signals.columns)
    if missing:
        raise ValueError(f"signals missing required columns: {sorted(missing)}")
    frame = signals.copy()
    for column in ("signal_date", "entry_date"):
        frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
    if frame[["signal_date", "entry_date"]].isna().any().any():
        raise ValueError("signal_date and entry_date must be valid timestamps")
    if (frame["signal_date"] > as_of).any():
        raise ValueError("future signal_date cannot be added to an account state")
    if (frame["entry_date"] <= frame["signal_date"]).any():
        raise ValueError("entry_date must be after signal_date")
    return frame.reset_index(drop=True)


def _signal_key(record: dict) -> tuple[str, str, str]:
    return (
        _utc_timestamp(record["signal_date"]).isoformat(),
        _utc_timestamp(record["entry_date"]).isoformat(),
        str(record["symbol"]),
    )


def _merge_signal_history(previous: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    records: dict[tuple[str, str, str], dict] = {}
    hashes: dict[tuple[str, str, str], str] = {}
    for frame in (previous, incoming):
        for record in frame.to_dict(orient="records"):
            key = _signal_key(record)
            record_hash = stable_payload_hash(record)
            if key in hashes and hashes[key] != record_hash:
                raise ValueError("an existing signal cannot be replaced with different content")
            records[key] = record
            hashes[key] = record_hash
    if not records:
        return _empty_signal_frame()
    return pd.DataFrame(records.values()).sort_values(
        ["signal_date", "entry_date", "symbol"]
    ).reset_index(drop=True)


def _previous_signal_history(
    previous_state: dict | None,
    rule: ForwardAccountRule,
    as_of: pd.Timestamp,
) -> pd.DataFrame:
    if previous_state is None:
        return _empty_signal_frame()
    if previous_state.get("account_name") != rule.account_name:
        raise ValueError("previous account state belongs to a different account")
    if previous_state.get("strategy_version") != rule.strategy_version:
        raise ValueError("previous account state uses a different strategy version")
    if previous_state.get("initial_cash") != rule.initial_cash:
        raise ValueError("previous account state has a different initial cash balance")
    previous_as_of = previous_state.get("state_as_of")
    if previous_as_of is not None and _utc_timestamp(previous_as_of) > as_of:
        raise ValueError("account state cannot move backwards in time")
    history = previous_state.get(
        "base_signal_history",
        previous_state.get("signal_history", _empty_signal_frame()),
    )
    if isinstance(history, pd.DataFrame):
        return history.copy()
    return pd.DataFrame(history)


def _apply_rule(signals: pd.DataFrame, rule: ForwardAccountRule) -> pd.DataFrame:
    frame = signals.copy()
    if frame.empty:
        return frame
    frame["stop_loss"] = rule.stop_loss
    frame["take_profit"] = rule.take_profit
    frame["maximum_holding_days"] = rule.maximum_holding_days
    frame["account_name"] = rule.account_name
    frame["strategy_version"] = rule.strategy_version
    return frame


def _pending_orders(
    signal_history: pd.DataFrame,
    last_session: pd.Timestamp | None,
) -> pd.DataFrame:
    if signal_history.empty:
        return signal_history.copy()
    entry_dates = pd.to_datetime(signal_history["entry_date"], utc=True)
    if last_session is None:
        return signal_history.copy()
    return signal_history.loc[entry_dates > last_session].copy().reset_index(drop=True)


def advance_forward_accounts_as_of(
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    *,
    as_of: Any,
    previous_states: dict[str, dict] | None = None,
    assumptions: ExecutionAssumptions | None = None,
    market_impact: MarketImpactAssumptions | None = None,
    risk_rules: PortfolioRiskRules | None = None,
    corporate_actions: pd.DataFrame | None = None,
    corporate_action_coverage: pd.DataFrame | None = None,
    corporate_action_policy: CorporateActionPolicy | None = None,
    asset_lifecycle: pd.DataFrame | None = None,
    asset_universe_coverage: pd.DataFrame | None = None,
    asset_lifecycle_policy: AssetLifecyclePolicy | None = None,
    fx_rates: pd.DataFrame | None = None,
    fx_accounting_policy: FxAccountingPolicy | None = None,
    tax_accounting_policy: TaxAccountingPolicy | None = None,
    account_rules: tuple[ForwardAccountRule, ...] | None = None,
) -> dict:
    """Advance independent short/mid virtual accounts through ``as_of``.

    Until NOW-P0-3 adds an append-only database ledger, state is reconstructed
    deterministically from immutable signal history and price rows known at the
    requested time. Passing the prior result carries each account's independent
    signal history into the next session; no balance is shared or transferred.
    """

    cutoff = _utc_timestamp(as_of)
    incoming = _normalize_signals(signals, cutoff)
    known_prices = known_prices_as_of(prices, cutoff)
    known_fx_rates = known_prices_as_of(fx_rates, cutoff) if fx_rates is not None else None
    if not known_prices.empty:
        known_prices = known_prices.copy()
        known_prices["price_time"] = pd.to_datetime(
            known_prices["price_time"], utc=True
        ).dt.normalize()
        last_session = known_prices["price_time"].max()
    else:
        last_session = None

    assumptions = assumptions or ExecutionAssumptions(
        fee_rate=0.001,
        spread_rate=0.001,
        tax_rate=0.0,
        lot_size=100,
        maximum_positions=2,
        maximum_position_rate=0.30,
    )
    market_impact = market_impact or MarketImpactAssumptions(
        require_volume=True,
        minimum_previous_turnover=50_000_000,
        use_turnover_cost_model=True,
    )
    risk_rules = risk_rules or PortfolioRiskRules(
        maximum_sector_rate=0.50,
        maximum_position_correlation=0.85,
    )
    rules = account_rules or FORWARD_ACCOUNT_RULES
    if not rules:
        raise ValueError("at least one forward account rule is required")
    account_names = [rule.account_name for rule in rules]
    if len(account_names) != len(set(account_names)):
        raise ValueError("forward account rule names must be unique")
    prior = previous_states or {}
    unknown_accounts = set(prior).difference(
        account_names
    )
    if unknown_accounts:
        raise ValueError(f"unknown previous account states: {sorted(unknown_accounts)}")

    accounts: dict[str, dict] = {}
    for rule in rules:
        previous_history = _previous_signal_history(
            prior.get(rule.account_name), rule, cutoff
        )
        normalized_previous = _normalize_signals(previous_history, cutoff)
        signal_history = _merge_signal_history(normalized_previous, incoming)
        ruled_signals = _apply_rule(signal_history, rule)
        result = simulate_ohlc_portfolio(
            ruled_signals,
            known_prices,
            initial_cash=rule.initial_cash,
            account_name=rule.account_name,
            assumptions=assumptions,
            market_impact=market_impact,
            risk_rules=risk_rules,
            strategy_version=rule.strategy_version,
            execution_version=FORWARD_EXECUTION_VERSION,
            corporate_actions=corporate_actions,
            corporate_action_coverage=corporate_action_coverage,
            corporate_action_policy=corporate_action_policy,
            asset_lifecycle=asset_lifecycle,
            asset_universe_coverage=asset_universe_coverage,
            asset_lifecycle_policy=asset_lifecycle_policy,
            fx_rates=known_fx_rates,
            fx_accounting_policy=fx_accounting_policy,
            tax_accounting_policy=tax_accounting_policy,
        )
        pending = _pending_orders(ruled_signals, last_session)
        cumulative_pnl = (
            None
            if result["equity"] is None
            else float(result["equity"] - rule.initial_cash)
        )
        result.update(
            {
                "label": rule.label,
                "state_version": FORWARD_ACCOUNT_STATE_VERSION,
                "strategy_version": rule.strategy_version,
                "state_as_of": cutoff,
                "last_market_session": last_session,
                "base_signal_history": signal_history,
                "signal_history": ruled_signals,
                "pending_orders": pending,
                "cumulative_pnl": cumulative_pnl,
                "maximum_drawdown": result["metrics"]["maximum_drawdown"],
                "account_rule": asdict(rule),
                "carry_forward": {
                    "cash": result["cash"],
                    "positions": result["positions"],
                    "realized_pnl": result["realized_pnl"],
                    "unrealized_pnl": result["unrealized_pnl"],
                    "cumulative_pnl": cumulative_pnl,
                    "maximum_drawdown": result["metrics"]["maximum_drawdown"],
                },
            }
        )
        accounts[rule.account_name] = result

    return {
        "state_version": FORWARD_ACCOUNT_STATE_VERSION,
        "state_as_of": cutoff,
        "initial_cash_each": 2_500_000.0,
        "currency": "JPY",
        "transfer_between_accounts": False,
        "known_prices": known_prices,
        "accounts": accounts,
    }
