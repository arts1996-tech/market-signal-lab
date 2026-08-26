"""Run auditable, cash-only historical simulations for a frozen selected universe."""

from dataclasses import dataclass

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.market_calendar import latest_contiguous_exchange_observations
from app.analysis.virtual_trading import build_point_in_time_historical_signals
from app.backtest.audit import EXECUTION_VERSION, frame_hash, json_value, stable_payload_hash
from app.backtest.ohlc import MarketImpactAssumptions, PortfolioRiskRules, simulate_ohlc_portfolio
from app.backtest.portfolio import ExecutionAssumptions
from app.database.models import (
    Asset,
    SelectedUniverseBacktestAssetResult,
    SelectedUniverseBacktestRun,
    UserAssetSelectionAnalysisResult,
    UserAssetSelectionAnalysisRun,
)
from app.database.repositories import market_prices_frame


SELECTED_UNIVERSE_SCOPE = "selected_universe_portfolio"
SELECTED_UNIVERSE_STRATEGY_VERSION = "selected-universe-cash-historical-v1"
INITIAL_CASH_JPY = 2_500_000.0


@dataclass(frozen=True)
class SelectedUniverseBacktestRule:
    horizon: str
    score_threshold: int
    stop_loss: float
    take_profit: float
    maximum_holding_days: int
    required_contiguous_sessions: int


SELECTED_UNIVERSE_BACKTEST_RULES = {
    "short_term": SelectedUniverseBacktestRule(
        horizon="short_term",
        score_threshold=70,
        stop_loss=-0.05,
        take_profit=0.08,
        maximum_holding_days=10,
        required_contiguous_sessions=80,
    ),
    "mid_term": SelectedUniverseBacktestRule(
        horizon="mid_term",
        score_threshold=70,
        stop_loss=-0.10,
        take_profit=0.18,
        maximum_holding_days=60,
        required_contiguous_sessions=140,
    ),
}


def _execution_configuration() -> tuple[ExecutionAssumptions, MarketImpactAssumptions, PortfolioRiskRules]:
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


def selected_asset_eligibility_rows(selection_assets: list[dict], prices: pd.DataFrame, *, rule: SelectedUniverseBacktestRule) -> list[dict]:
    """Keep every selected asset visible; do not silently drop unsafe inputs."""

    rows = []
    for asset in selection_assets:
        reasons = list(asset.get("analysis_reasons") or [])
        is_jpx_jpy = asset.get("exchange") == "JPX" and asset.get("currency") == "JPY"
        if not is_jpx_jpy:
            reasons.append("cross_market_cash_simulation_not_yet_supported")
        if asset.get("analysis_status") != "analyzed":
            reasons.append("analysis_snapshot_not_eligible")
        symbol_prices = prices[prices["symbol"] == asset["symbol"]].copy() if not prices.empty else pd.DataFrame()
        if not symbol_prices.empty:
            required = ["price_time", "open", "high", "low", "close", "volume"]
            if not set(required).issubset(symbol_prices.columns):
                symbol_prices = pd.DataFrame()
            else:
                for column in required[1:]:
                    symbol_prices[column] = pd.to_numeric(symbol_prices[column], errors="coerce")
                symbol_prices["price_time"] = pd.to_datetime(symbol_prices["price_time"], utc=True)
                symbol_prices = symbol_prices.dropna(subset=required)
                symbol_prices = symbol_prices[(symbol_prices[["open", "high", "low", "close"]] > 0).all(axis=1)]
                symbol_prices = symbol_prices[symbol_prices["volume"] >= 0]
        sessions = (
            latest_contiguous_exchange_observations(symbol_prices["price_time"], "XTKS")
            if not symbol_prices.empty
            else 0
        )
        if sessions < rule.required_contiguous_sessions:
            reasons.append("insufficient_contiguous_price_history")
        rows.append(
            {
                **asset,
                "contiguous_sessions": sessions,
                "status": "eligible" if not reasons else "insufficient_data",
                "reason_codes": sorted(set(reasons)),
            }
        )
    return rows


def _records(frame: pd.DataFrame) -> list[dict]:
    return [] if frame.empty else json_value(frame.to_dict(orient="records"))


def _source_assets(session: Session, snapshot: UserAssetSelectionAnalysisRun) -> list[dict]:
    rows = session.execute(
        select(UserAssetSelectionAnalysisResult, Asset)
        .join(Asset, Asset.id == UserAssetSelectionAnalysisResult.asset_id)
        .where(UserAssetSelectionAnalysisResult.run_id == snapshot.id)
    ).all()
    return [
        {
            "asset_id": result.asset_id,
            "symbol": asset.symbol,
            "exchange": asset.exchange or "",
            "currency": asset.currency,
            "analysis_status": result.analysis_status,
            "analysis_reasons": result.quality_reasons or [],
        }
        for result, asset in rows
    ]


def run_selected_universe_backtest(
    session: Session,
    *,
    analysis_snapshot_run_id: str,
    horizon: str,
) -> tuple[SelectedUniverseBacktestRun, bool]:
    """Persist one cash-only retrospective simulation without touching virtual accounts."""

    rule = SELECTED_UNIVERSE_BACKTEST_RULES.get(horizon)
    if rule is None:
        raise ValueError("horizon must be short_term or mid_term")
    snapshot = session.get(UserAssetSelectionAnalysisRun, analysis_snapshot_run_id)
    if snapshot is None:
        raise ValueError("selected-universe analysis snapshot does not exist")
    selected_assets = _source_assets(session, snapshot)
    if not selected_assets:
        raise ValueError("selected-universe analysis snapshot has no assets")
    symbols = [asset["symbol"] for asset in selected_assets]
    prices = market_prices_frame(session, symbols, source_policy=snapshot.data_scope)
    eligibility = selected_asset_eligibility_rows(selected_assets, prices, rule=rule)
    eligible_symbols = [row["symbol"] for row in eligibility if row["status"] == "eligible"]
    simulation_prices = prices[prices["symbol"].isin(eligible_symbols)].copy() if eligible_symbols else pd.DataFrame()
    index_prices = market_prices_frame(
        session,
        ["NIKKEI225", "NASDAQCOM", "DJIA", "SP500"],
        source_policy=snapshot.data_scope,
    )
    assumptions, market_impact, risk_rules = _execution_configuration()
    input_data_version = stable_payload_hash(
        {
            "prices": frame_hash(simulation_prices),
            "index_prices": frame_hash(index_prices),
            "analysis_snapshot_hash": snapshot.snapshot_hash,
            "eligibility": eligibility,
        }
    )
    simulation_hash = stable_payload_hash(
        {
            "scope": SELECTED_UNIVERSE_SCOPE,
            "rule": rule,
            "trade_mode": "cash",
            "initial_cash": INITIAL_CASH_JPY,
            "input_data_version": input_data_version,
            "assumptions": assumptions,
            "market_impact": market_impact,
            "risk_rules": risk_rules,
        }
    )
    existing = session.scalar(
        select(SelectedUniverseBacktestRun).where(
            SelectedUniverseBacktestRun.analysis_snapshot_run_id == snapshot.id,
            SelectedUniverseBacktestRun.horizon == horizon,
            SelectedUniverseBacktestRun.simulation_hash == simulation_hash,
        )
    )
    if existing is not None:
        return existing, False

    reasons: list[str] = [
        "retrospective_user_selected",
        "cash_long_only",
        "not_forward_performance_evidence",
    ]
    result: dict = {
        "eligibility": eligibility,
        "evaluation_classification": "retrospective_user_selected",
    }
    if not eligible_symbols:
        reasons.append("no_assets_passed_selected_universe_quality_gate")
        status = "insufficient_data"
        signals = pd.DataFrame()
        simulation = None
    else:
        signals = build_point_in_time_historical_signals(
            index_prices,
            simulation_prices,
            score_threshold=rule.score_threshold,
            stop_loss=rule.stop_loss,
            take_profit=rule.take_profit,
            maximum_holding_days=rule.maximum_holding_days,
            min_observations=30,
        )
        signals = signals[signals["side"] == "long"].copy() if not signals.empty else signals
        if signals.empty:
            reasons.append("no_historical_long_signals")
            status = "insufficient_data"
            simulation = None
        else:
            simulation = simulate_ohlc_portfolio(
                signals,
                simulation_prices,
                initial_cash=INITIAL_CASH_JPY,
                account_name=f"selected_universe_{horizon}",
                assumptions=assumptions,
                market_impact=market_impact,
                risk_rules=risk_rules,
                input_data_version=input_data_version,
                strategy_version=SELECTED_UNIVERSE_STRATEGY_VERSION,
                execution_version=EXECUTION_VERSION,
            )
            status = "success"
            result.update(
                {
                    "summary": {
                        key: json_value(simulation[key])
                        for key in ("account_name", "initial_cash", "cash", "equity", "realized_pnl", "unrealized_pnl", "metrics", "manifest", "risk_halted")
                    },
                    "transactions": _records(simulation["transactions"]),
                    "daily_states": _records(simulation["snapshots"]),
                    "rejected_signals": _records(simulation["rejected_signals"]),
                    "decision_cards": _records(simulation["decision_cards"]),
                }
            )

    period_start = None if simulation_prices.empty else pd.to_datetime(simulation_prices["price_time"], utc=True).min().date()
    period_end = None if simulation_prices.empty else pd.to_datetime(simulation_prices["price_time"], utc=True).max().date()
    run = SelectedUniverseBacktestRun(
        analysis_snapshot_run_id=snapshot.id,
        selection_id=snapshot.selection_id,
        selection_key=snapshot.selection_key,
        selection_version=snapshot.selection_version,
        selection_composition_hash=snapshot.selection_composition_hash,
        scope=SELECTED_UNIVERSE_SCOPE,
        horizon=horizon,
        trade_mode="cash",
        initial_cash=INITIAL_CASH_JPY,
        strategy_version=SELECTED_UNIVERSE_STRATEGY_VERSION,
        execution_version=EXECUTION_VERSION,
        data_scope=snapshot.data_scope,
        input_data_version=input_data_version,
        simulation_hash=simulation_hash,
        period_start=period_start,
        period_end=period_end,
        status=status,
        evaluation_classification="retrospective_user_selected",
        reasons=reasons,
        result=result,
    )
    session.add(run)
    session.flush()
    transactions = pd.DataFrame() if simulation is None else simulation["transactions"]
    for item in eligibility:
        symbol_transactions = transactions[transactions.get("symbol", pd.Series(dtype=str)) == item["symbol"]] if not transactions.empty else pd.DataFrame()
        realized = None
        if not symbol_transactions.empty and "realized_pnl" in symbol_transactions:
            realized = float(pd.to_numeric(symbol_transactions["realized_pnl"], errors="coerce").sum())
        item_signals = signals[signals["symbol"] == item["symbol"]] if not signals.empty else pd.DataFrame()
        session.add(
            SelectedUniverseBacktestAssetResult(
                run_id=run.id,
                asset_id=item["asset_id"],
                status=item["status"],
                reason_codes=item["reason_codes"],
                signal_count=len(item_signals),
                transaction_count=len(symbol_transactions),
                realized_pnl=realized,
                result={"contiguous_sessions": item["contiguous_sessions"]},
            )
        )
    session.flush()
    return run, True
