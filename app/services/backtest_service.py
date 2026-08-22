"""Real-data walk-forward backtest orchestration with explicit quality gates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from app.analysis.market_calendar import (
    latest_contiguous_exchange_observations,
    next_exchange_session,
)
from app.analysis.virtual_trading import (
    HISTORICAL_SIGNAL_VERSION,
    build_point_in_time_historical_signals,
)
from app.backtest.audit import frame_hash, json_value, stable_payload_hash
from app.backtest.asset_lifecycle import AssetLifecyclePolicy, evaluate_asset_lifecycle_gate
from app.backtest.fx_accounting import FxAccountingPolicy, evaluate_fx_gate
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
from app.backtest.segmented_evaluation import (
    SegmentedEvaluationPolicy,
    classify_completed_trades,
    summarize_segmented_trades,
)
from app.backtest.tax_accounting import TaxAccountingPolicy
from app.backtest.validation import (
    WALK_FORWARD_PROTOCOL_VERSION,
    evaluate_frozen_strategy_walk_forward,
)
from app.backtest.validation_registry import (
    claim_forward_period,
    forward_period_activation,
)
from app.core.config import get_settings
from app.database.repositories import (
    asset_lifecycle_frame,
    asset_universe_coverage_frame,
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


def _latest_contiguous_prices(
    prices: pd.DataFrame, counts: dict[str, int], qualified_symbols: list[str]
) -> pd.DataFrame:
    """Discard older disconnected history before walk-forward evaluation."""

    frames = []
    for symbol in qualified_symbols:
        count = counts.get(symbol, 0)
        if count <= 0:
            continue
        group = prices[prices["symbol"] == symbol].sort_values("price_time")
        frames.append(group.tail(count))
    if not frames:
        return pd.DataFrame(columns=prices.columns)
    return pd.concat(frames, ignore_index=True)


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
    asset_lifecycle: pd.DataFrame | None = None,
    asset_universe_coverage: pd.DataFrame | None = None,
    fx_rates: pd.DataFrame | None = None,
) -> dict:
    prices = _valid_ohlcv(japan_prices)
    counts = _contiguous_counts(prices)
    assumptions, market_impact, risk_rules = _execution_configuration()
    segmented_policy = SegmentedEvaluationPolicy(
        high_turnover_threshold=market_impact.high_turnover_threshold,
        medium_turnover_threshold=market_impact.medium_turnover_threshold,
        low_turnover_threshold=market_impact.low_turnover_threshold,
    )
    corporate_action_policy = CorporateActionPolicy()
    corporate_action_gate = evaluate_corporate_action_gate(
        prices,
        corporate_actions,
        corporate_action_coverage,
        corporate_action_policy,
    )
    lifecycle_enabled = asset_lifecycle is not None or asset_universe_coverage is not None
    asset_lifecycle_policy = AssetLifecyclePolicy(
        missing_coverage_policy="reject" if lifecycle_enabled else "warn"
    )
    asset_lifecycle_gate = evaluate_asset_lifecycle_gate(
        prices, asset_lifecycle, asset_universe_coverage, asset_lifecycle_policy
    )
    fx_accounting_policy = FxAccountingPolicy()
    fx_gate = evaluate_fx_gate(prices, fx_rates, fx_accounting_policy)
    tax_accounting_policy = TaxAccountingPolicy()
    input_data_version = frame_hash(prices)
    rule_hash = stable_payload_hash(
        {
            "rule": rule,
            "assumptions": assumptions,
            "market_impact": market_impact,
            "risk_rules": risk_rules,
            "corporate_action_policy": corporate_action_policy,
            "asset_lifecycle_policy": asset_lifecycle_policy,
            "fx_accounting_policy": fx_accounting_policy,
            "tax_accounting_policy": tax_accounting_policy,
            "walk_forward_protocol_version": WALK_FORWARD_PROTOCOL_VERSION,
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
        "walk_forward_protocol_version": WALK_FORWARD_PROTOCOL_VERSION,
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
        "asset_lifecycle_gate": {
            key: json_value(value)
            for key, value in asset_lifecycle_gate.items()
            if key not in {"records", "coverage"}
        },
        "fx_gate": {
            key: json_value(value)
            for key, value in fx_gate.items()
            if key != "rates"
        },
        "tax_accounting_policy": json_value(tax_accounting_policy),
        "period_separation": {
            "training": "expanding_historical_training",
            "validation": "registered_unseen_historical_windows",
            "forward": "separate_current_market_observation_after_validation",
            "validation_reused_as_forward": False,
        },
        "signal_generation_version": HISTORICAL_SIGNAL_VERSION,
        "historical_signal_availability_basis": (
            "session_date_only; provider publication timestamp is not reconstructed"
        ),
        "segmented_evaluation": summarize_segmented_trades(
            pd.DataFrame(), policy=segmented_policy
        ),
    }
    if not qualified_symbols:
        return {
            **base_details,
            "status": "insufficient_data",
            "reasons": ["insufficient_contiguous_price_history"],
            "windows": [],
        }

    prices = _latest_contiguous_prices(prices, counts, qualified_symbols)
    benchmark = _benchmark(index_prices)
    if benchmark is None:
        return {
            **base_details,
            "status": "insufficient_data",
            "reasons": ["nikkei_benchmark_missing"],
            "windows": [],
        }
    signals = build_point_in_time_historical_signals(
        index_prices,
        prices,
        score_threshold=rule.score_threshold,
        stop_loss=rule.stop_loss,
        take_profit=rule.take_profit,
        maximum_holding_days=rule.maximum_holding_days,
        min_observations=30,
    )
    if signals.empty:
        return {
            **base_details,
            "status": "insufficient_data",
            "reasons": ["no_eligible_historical_signals"],
            "windows": [],
        }

    segmented_trade_frames: list[pd.DataFrame] = []
    simulated_window_count = 0

    def simulator(test_signals: pd.DataFrame, prices_as_of_test: pd.DataFrame) -> dict:
        nonlocal simulated_window_count
        simulated_window_count += 1
        simulation_input_data_version = frame_hash(prices_as_of_test)
        simulation_result = simulate_ohlc_portfolio(
            test_signals,
            prices_as_of_test,
            initial_cash=2_500_000,
            account_name=rule.account_name,
            assumptions=assumptions,
            market_impact=market_impact,
            risk_rules=risk_rules,
            benchmark=benchmark,
            input_data_version=simulation_input_data_version,
            strategy_version=rule.strategy_version,
            corporate_actions=corporate_actions,
            corporate_action_coverage=corporate_action_coverage,
            corporate_action_policy=corporate_action_policy,
            asset_lifecycle=asset_lifecycle,
            asset_universe_coverage=asset_universe_coverage,
            asset_lifecycle_policy=asset_lifecycle_policy,
            fx_rates=fx_rates,
            fx_accounting_policy=fx_accounting_policy,
            tax_accounting_policy=tax_accounting_policy,
        )
        classified = classify_completed_trades(
            simulation_result,
            index_prices,
            policy=segmented_policy,
            validation_window=simulated_window_count,
        )
        if not classified.empty:
            segmented_trade_frames.append(classified)
        return simulation_result

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
    segmented_trades = (
        pd.concat(segmented_trade_frames, ignore_index=True)
        if segmented_trade_frames
        else pd.DataFrame()
    )
    segmented_evaluation = summarize_segmented_trades(
        segmented_trades, policy=segmented_policy
    )
    forward_period = {
        "status": "not_activated",
        "decision_track": "current_market",
        "validation_data_reused": False,
        "observed_sessions": 0,
    }
    if not walk_forward.empty:
        validation_end = pd.Timestamp(walk_forward["test_end"].max())
        observed_through = pd.to_datetime(
            prices["price_time"], utc=True
        ).max().normalize()
        activation = forward_period_activation(
            validation_registry_path, evaluation_track=rule.account_name
        )
        if activation is None:
            forward_start = next_exchange_session(observed_through, "XTKS")
        else:
            validation_end = pd.Timestamp(activation["validation_end"])
            observed_through = pd.Timestamp(
                activation.get("observed_through", activation["validation_end"])
            )
            forward_start = pd.Timestamp(activation["forward_start"])
        if forward_start is not None:
            forward_start = pd.Timestamp(forward_start)
            forward_start = (
                forward_start.tz_localize("UTC")
                if forward_start.tzinfo is None
                else forward_start.tz_convert("UTC")
            )
            frozen_rule_hash = str(walk_forward.iloc[0]["frozen_rule_hash"])
            if activation is None:
                activation = claim_forward_period(
                    validation_registry_path,
                    strategy_version=rule.strategy_version,
                    rule_hash=rule_hash,
                    frozen_rule_hash=frozen_rule_hash,
                    evaluation_track=rule.account_name,
                    validation_end=validation_end,
                    observed_through=observed_through,
                    forward_start=forward_start,
                    protocol_version=WALK_FORWARD_PROTOCOL_VERSION,
                )
            observed_forward = prices[
                pd.to_datetime(prices["price_time"], utc=True).dt.normalize()
                >= pd.Timestamp(forward_start)
            ]
            observed_forward_sessions = int(observed_forward["price_time"].nunique())
            forward_period = {
                "status": (
                    "reserved_unscored"
                    if observed_forward_sessions
                    else "awaiting_observations"
                ),
                "decision_track": "current_market",
                "validation_data_reused": False,
                "validation_end": validation_end,
                "historical_data_observed_through": observed_through,
                "embargoed_sessions_after_validation": int(
                    prices.loc[
                        pd.to_datetime(prices["price_time"], utc=True).dt.normalize()
                        > validation_end,
                        "price_time",
                    ].nunique()
                ),
                "forward_start": forward_start,
                "observed_sessions": observed_forward_sessions,
                "scored_in_historical_validation": False,
                "frozen_rule_hash": frozen_rule_hash,
                "activation_claim_id": activation["claim_id"],
            }
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
        "segmented_evaluation": segmented_evaluation,
        "forward_period": json_value(forward_period),
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
        ["NIKKEI225", "NASDAQCOM", "DJIA", "SP500", "DEXJPUS"],
        source_policy=source_policy,
    )
    corporate_actions = corporate_actions_frame(session, symbols)
    corporate_action_coverage = corporate_action_coverage_frame(session, symbols)
    asset_lifecycle = asset_lifecycle_frame(session, symbols)
    asset_universe_coverage = asset_universe_coverage_frame(session)
    fx_rates = (
        index_prices[index_prices["symbol"] == "DEXJPUS"].copy()
        if not index_prices.empty
        else pd.DataFrame()
    )
    accounts = {
        rule.account_name: evaluate_real_account_walk_forward(
            japan_prices,
            index_prices,
            rule=rule,
            validation_registry_path=validation_registry_path,
            corporate_actions=corporate_actions,
            corporate_action_coverage=corporate_action_coverage,
            asset_lifecycle=asset_lifecycle,
            asset_universe_coverage=asset_universe_coverage,
            fx_rates=fx_rates,
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
