"""Real-data walk-forward backtest orchestration with explicit quality gates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from app.analysis.market_calendar import latest_contiguous_exchange_observations
from app.analysis.virtual_trading import (
    build_virtual_trades,
    virtual_signals_from_reference_trades,
)
from app.backtest.audit import frame_hash, json_value, stable_payload_hash
from app.backtest.corporate_actions import (
    CorporateActionPolicy,
    evaluate_corporate_action_gate,
)
from app.backtest.ohlc import (
    MarketImpactAssumptions,
    PortfolioRiskRules,
    simulate_ohlc_portfolio,
)
from app.backtest.portfolio import ExecutionAssumptions
from app.backtest.validation import evaluate_frozen_strategy_walk_forward
from app.core.config import get_settings
from app.database.repositories import (
    corporate_action_coverage_frame,
    corporate_actions_frame,
    list_assets_by_source,
    market_prices_frame,
)


@dataclass(frozen=True)
class RealBacktestRule:
    account_name: str
    strategy_version: str
    score_threshold: int
    stop_loss: float
    take_profit: float
    maximum_holding_days: int
    minimum_train_sessions: int
    test_sessions: int

    @property
    def required_contiguous_sessions(self) -> int:
        return self.minimum_train_sessions + self.test_sessions


REAL_BACKTEST_RULES = (
    RealBacktestRule(
        account_name="short_term",
        strategy_version="real-short-walk-forward-v1",
        score_threshold=70,
        stop_loss=-0.05,
        take_profit=0.08,
        maximum_holding_days=10,
        minimum_train_sessions=60,
        test_sessions=20,
    ),
    RealBacktestRule(
        account_name="mid_term",
        strategy_version="real-mid-walk-forward-v1",
        score_threshold=70,
        stop_loss=-0.10,
        take_profit=0.18,
        maximum_holding_days=60,
        minimum_train_sessions=120,
        test_sessions=20,
    ),
)


def _execution_configuration() -> tuple[
    ExecutionAssumptions, MarketImpactAssumptions, PortfolioRiskRules
]:
    return (
        ExecutionAssumptions(
            fee_rate=0.001,
            spread_rate=0.001,
            tax_rate=0.0,
            lot_size=100,
            maximum_positions=2,
            maximum_position_rate=0.30,
        ),
        MarketImpactAssumptions(
            require_volume=True,
            minimum_previous_turnover=50_000_000,
            use_turnover_cost_model=True,
        ),
        PortfolioRiskRules(
            maximum_sector_rate=0.50,
            maximum_position_correlation=0.85,
        ),
    )


def _valid_ohlcv(prices: pd.DataFrame) -> pd.DataFrame:
    if prices.empty:
        return prices
    frame = prices.copy()
    required = ("open", "high", "low", "close", "volume")
    for column in required:
        if column not in frame:
            return pd.DataFrame(columns=frame.columns)
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["price_time"] = pd.to_datetime(frame["price_time"], utc=True).dt.normalize()
    valid = frame[list(required)].notna().all(axis=1)
    valid &= (frame[["open", "high", "low", "close"]] > 0).all(axis=1)
    valid &= frame["volume"] >= 0
    return frame.loc[valid].copy()


def _contiguous_counts(prices: pd.DataFrame) -> dict[str, int]:
    return {
        str(symbol): latest_contiguous_exchange_observations(group["price_time"], "XTKS")
        for symbol, group in prices.groupby("symbol")
    }


def _benchmark(index_prices: pd.DataFrame) -> pd.Series | None:
    if index_prices.empty:
        return None
    nikkei = index_prices[index_prices["symbol"] == "NIKKEI225"].copy()
    if nikkei.empty:
        return None
    nikkei["price_time"] = pd.to_datetime(nikkei["price_time"], utc=True).dt.normalize()
    result = (
        nikkei.drop_duplicates("price_time", keep="last")
        .set_index("price_time")["close"]
        .sort_index()
    )
    result = pd.to_numeric(result, errors="coerce").dropna()
    return result if len(result) >= 2 else None


def _window_records(frame: pd.DataFrame) -> list[dict]:
    if frame.empty:
        return []
    return json_value(frame.to_dict(orient="records"))


def evaluate_real_account_walk_forward(
    japan_prices: pd.DataFrame,
    index_prices: pd.DataFrame,
    *,
    rule: RealBacktestRule,
    validation_registry_path: str | Path,
    corporate_actions: pd.DataFrame | None = None,
    corporate_action_coverage: pd.DataFrame | None = None,
) -> dict:
    prices = _valid_ohlcv(japan_prices)
    counts = _contiguous_counts(prices)
    assumptions, market_impact, risk_rules = _execution_configuration()
    corporate_action_policy = CorporateActionPolicy()
    corporate_action_gate = evaluate_corporate_action_gate(
        prices,
        corporate_actions,
        corporate_action_coverage,
        corporate_action_policy,
    )
    input_data_version = frame_hash(prices)
    rule_hash = stable_payload_hash(
        {
            "rule": rule,
            "assumptions": assumptions,
            "market_impact": market_impact,
            "risk_rules": risk_rules,
            "corporate_action_policy": corporate_action_policy,
        }
    )
    qualified_symbols = sorted(
        symbol
        for symbol, count in counts.items()
        if count >= rule.required_contiguous_sessions
    )
    base_details = {
        "account_name": rule.account_name,
        "strategy_version": rule.strategy_version,
        "rule": asdict(rule),
        "required_contiguous_sessions": rule.required_contiguous_sessions,
        "maximum_contiguous_sessions": max(counts.values(), default=0),
        "qualified_symbols": len(qualified_symbols),
        "observed_symbols": len(counts),
        "input_data_version": input_data_version,
        "rule_hash": rule_hash,
        "validation_registry_path": str(validation_registry_path),
        "benchmark": "NIKKEI225",
        "execution_assumptions": json_value(assumptions),
        "market_impact_assumptions": json_value(market_impact),
        "risk_rules": json_value(risk_rules),
        "corporate_action_gate": {
            key: json_value(value)
            for key, value in corporate_action_gate.items()
            if key not in {"actions", "coverage"}
        },
    }
    if not qualified_symbols:
        return {
            **base_details,
            "status": "insufficient_data",
            "reasons": ["insufficient_contiguous_price_history"],
            "windows": [],
        }

    prices = prices[prices["symbol"].isin(qualified_symbols)].copy()
    benchmark = _benchmark(index_prices)
    if benchmark is None:
        return {
            **base_details,
            "status": "insufficient_data",
            "reasons": ["nikkei_benchmark_missing"],
            "windows": [],
        }
    reference_trades = build_virtual_trades(
        index_prices,
        prices,
        score_threshold=rule.score_threshold,
        holding_days=rule.maximum_holding_days,
        min_observations=30,
        max_trades=None,
        recent_signal_sessions=None,
    )
    signals = virtual_signals_from_reference_trades(
        reference_trades,
        stop_loss=rule.stop_loss,
        take_profit=rule.take_profit,
        maximum_holding_days=rule.maximum_holding_days,
    )
    if signals.empty:
        return {
            **base_details,
            "status": "insufficient_data",
            "reasons": ["no_eligible_historical_signals"],
            "windows": [],
        }

    def simulator(test_signals: pd.DataFrame, prices_as_of_test: pd.DataFrame) -> dict:
        return simulate_ohlc_portfolio(
            test_signals,
            prices_as_of_test,
            initial_cash=2_500_000,
            account_name=rule.account_name,
            assumptions=assumptions,
            market_impact=market_impact,
            risk_rules=risk_rules,
            benchmark=benchmark,
            input_data_version=input_data_version,
            strategy_version=rule.strategy_version,
            corporate_actions=corporate_actions,
            corporate_action_coverage=corporate_action_coverage,
            corporate_action_policy=corporate_action_policy,
        )

    walk_forward = evaluate_frozen_strategy_walk_forward(
        signals,
        prices,
        simulator,
        minimum_train_sessions=rule.minimum_train_sessions,
        test_sessions=rule.test_sessions,
        validation_registry_path=validation_registry_path,
        strategy_version=rule.strategy_version,
        rule_hash=rule_hash,
        evaluation_track=rule.account_name,
    )
    test_signals = int(walk_forward.get("test_signals", pd.Series(dtype=int)).sum())
    closed_trades = int(walk_forward.get("closed_trades", pd.Series(dtype=int)).sum())
    reasons = []
    if walk_forward.empty:
        reasons.append("no_walk_forward_windows")
    elif test_signals == 0:
        reasons.append("no_signals_in_validation_windows")
    elif closed_trades == 0:
        reasons.append("no_closed_validation_trades")
    return {
        **base_details,
        "status": "success" if not reasons else "insufficient_data",
        "reasons": reasons,
        "benchmark_warning": "TOPIXと単純保有の追加比較はNOW-P2-3で実装する",
        "signal_count": len(signals),
        "validation_window_count": len(walk_forward),
        "validation_test_signals": test_signals,
        "validation_closed_trades": closed_trades,
        "windows": _window_records(walk_forward),
    }


def run_real_walk_forward_backtest(
    session: Session,
    *,
    validation_registry_path: str | Path = "data/validation/live-windows.json",
) -> dict:
    """Run independent short/mid tracks or explain why real data is insufficient."""

    source_policy = "demo_only" if get_settings().market_data_mode == "demo" else "real_only"
    if source_policy != "real_only":
        raise ValueError("real walk-forward backtest requires real_only source policy")
    assets = list_assets_by_source(
        session,
        "jquants",
        asset_types=["stock", "etf"],
        limit=None,
    )
    symbols = [asset.symbol for asset in assets]
    japan_prices = market_prices_frame(session, symbols, source_policy=source_policy) if symbols else pd.DataFrame()
    index_prices = market_prices_frame(
        session,
        ["NIKKEI225", "NASDAQCOM", "DJIA", "SP500"],
        source_policy=source_policy,
    )
    corporate_actions = corporate_actions_frame(session, symbols)
    corporate_action_coverage = corporate_action_coverage_frame(session, symbols)
    accounts = {
        rule.account_name: evaluate_real_account_walk_forward(
            japan_prices,
            index_prices,
            rule=rule,
            validation_registry_path=validation_registry_path,
            corporate_actions=corporate_actions,
            corporate_action_coverage=corporate_action_coverage,
        )
        for rule in REAL_BACKTEST_RULES
    }
    overall_status = (
        "success"
        if accounts and all(account["status"] == "success" for account in accounts.values())
        else "insufficient_data"
    )
    return {
        "status": overall_status,
        "source_policy": source_policy,
        "asset_count": len(symbols),
        "price_rows": len(japan_prices),
        "validation_registry_path": str(validation_registry_path),
        "accounts": accounts,
        "warning": "研究用の過去検証であり、予測能力・将来利益・投資助言を示さない",
    }
