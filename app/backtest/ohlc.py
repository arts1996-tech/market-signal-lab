from dataclasses import dataclass
import math

import pandas as pd

from app.backtest.audit import build_run_manifest, decision_card
from app.backtest.asset_lifecycle import (
    DELISTED,
    INVESTABLE,
    NON_INVESTABLE,
    SUSPENDED,
    UNKNOWN,
    AssetLifecyclePolicy,
    evaluate_asset_lifecycle_gate,
    lifecycle_record_on,
)
from app.backtest.corporate_actions import (
    CASH_DIVIDEND,
    REVERSE_SPLIT,
    STOCK_SPLIT,
    UNSUPPORTED_ACTIONS,
    CorporateActionPolicy,
    evaluate_corporate_action_gate,
    events_on,
    known_unsupported_event_in_horizon,
)
from app.backtest.fx_accounting import (
    FxAccountingPolicy,
    evaluate_fx_gate,
    fx_execution_rate,
    fx_mid_on,
)
from app.backtest.portfolio import ExecutionAssumptions
from app.backtest.tax_accounting import TaxAccountingPolicy


@dataclass(frozen=True)
class MarketImpactAssumptions:
    require_volume: bool = False
    maximum_volume_participation: float = 0.10
    allow_partial_fill: bool = True
    base_slippage_rate: float = 0.0005
    impact_rate: float = 0.005
    minimum_previous_turnover: float = 0.0
    use_turnover_cost_model: bool = False
    high_turnover_threshold: float = 1_000_000_000.0
    medium_turnover_threshold: float = 250_000_000.0
    low_turnover_threshold: float = 50_000_000.0
    simultaneous_hit_policy: str = "stop_first"

    def __post_init__(self) -> None:
        if not 0 < self.maximum_volume_participation <= 1:
            raise ValueError("maximum_volume_participation must be between 0 and 1")
        if self.base_slippage_rate < 0 or self.impact_rate < 0:
            raise ValueError("slippage rates must be non-negative")
        if self.minimum_previous_turnover < 0:
            raise ValueError("minimum_previous_turnover must be non-negative")
        if not (
            self.high_turnover_threshold
            > self.medium_turnover_threshold
            > self.low_turnover_threshold
            > 0
        ):
            raise ValueError("turnover thresholds must be positive and descending")
        if self.simultaneous_hit_policy != "stop_first":
            raise ValueError("only conservative stop_first policy is supported")


@dataclass(frozen=True)
class PortfolioRiskRules:
    maximum_sector_rate: float = 0.50
    maximum_drawdown: float = 0.20
    maximum_risk_per_trade_rate: float = 0.01
    maximum_total_open_risk_rate: float = 0.05
    loss_streak_threshold: int = 3
    cooldown_sessions: int = 5
    maximum_position_correlation: float | None = None
    correlation_lookback_sessions: int = 60
    minimum_correlation_observations: int = 20

    def __post_init__(self) -> None:
        if not 0 < self.maximum_sector_rate <= 1:
            raise ValueError("maximum_sector_rate must be between 0 and 1")
        if not 0 < self.maximum_drawdown < 1:
            raise ValueError("maximum_drawdown must be between 0 and 1")
        if not 0 < self.maximum_risk_per_trade_rate < 1:
            raise ValueError("maximum_risk_per_trade_rate must be between 0 and 1")
        if not 0 < self.maximum_total_open_risk_rate < 1:
            raise ValueError("maximum_total_open_risk_rate must be between 0 and 1")
        if self.maximum_risk_per_trade_rate > self.maximum_total_open_risk_rate:
            raise ValueError("per-trade risk cannot exceed total open risk")
        if self.loss_streak_threshold <= 0 or self.cooldown_sessions < 0:
            raise ValueError("loss_streak_threshold must be positive and cooldown non-negative")
        if self.maximum_position_correlation is not None and not 0 < self.maximum_position_correlation <= 1:
            raise ValueError("maximum_position_correlation must be between 0 and 1")
        if self.correlation_lookback_sessions < 2:
            raise ValueError("correlation_lookback_sessions must be at least 2")
        if self.minimum_correlation_observations < 2:
            raise ValueError("minimum_correlation_observations must be at least 2")


def _empty_result(
    account_name: str,
    initial_cash: float,
    manifest: dict,
    assumptions: ExecutionAssumptions,
    market_impact: MarketImpactAssumptions,
    risk_rules: PortfolioRiskRules,
    corporate_action_policy: CorporateActionPolicy,
    corporate_action_gate: dict,
    asset_lifecycle_policy: AssetLifecyclePolicy,
    asset_lifecycle_gate: dict,
    fx_accounting_policy: FxAccountingPolicy,
    fx_gate: dict,
    tax_accounting_policy: TaxAccountingPolicy,
) -> dict:
    empty = pd.DataFrame()
    return {
        "account_name": account_name,
        "account_currency": fx_accounting_policy.account_currency,
        "initial_cash": float(initial_cash),
        "cash": float(initial_cash),
        "equity": float(initial_cash),
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "positions": empty,
        "transactions": empty,
        "snapshots": empty,
        "rejected_signals": empty,
        "decision_cards": empty,
        "benchmark_comparisons": empty,
        "metrics": {
            "total_return": 0.0,
            "maximum_drawdown": 0.0,
            "closed_trades": 0,
            "win_rate": None,
            "average_trade_return": None,
            "win_rate_ci95": None,
            "average_trade_return_ci95": None,
            "benchmark_return": None,
            "excess_return": None,
            "asset_price_pnl_jpy": 0.0,
            "fx_pnl_jpy": 0.0,
            "fx_conversion_cost_jpy": 0.0,
        },
        "manifest": manifest,
        "assumptions": assumptions,
        "market_impact": market_impact,
        "risk_rules": risk_rules,
        "risk_halted": False,
        "dividend_income": 0.0,
        "pending_dividends": empty,
        "corporate_action_events": empty,
        "corporate_action_policy": corporate_action_policy,
        "corporate_action_gate": {
            key: value
            for key, value in corporate_action_gate.items()
            if key not in {"actions", "coverage"}
        },
        "asset_lifecycle_events": empty,
        "asset_lifecycle_policy": asset_lifecycle_policy,
        "asset_lifecycle_gate": {
            key: value
            for key, value in asset_lifecycle_gate.items()
            if key not in {"records", "coverage"}
        },
        "fx_accounting_policy": fx_accounting_policy,
        "fx_gate": {
            key: value for key, value in fx_gate.items() if key != "rates"
        },
        "fx_events": empty,
        "tax_accounting_policy": tax_accounting_policy,
        "tax_summary": tax_accounting_policy.disclosure(),
        "quality_warnings": list(
            dict.fromkeys(
                corporate_action_gate["warnings"]
                + asset_lifecycle_gate["warnings"]
                + fx_gate["warnings"]
                + fx_gate["warnings"]
            )
        ),
        "evaluation_status": (
            "warning"
            if corporate_action_gate["warnings"]
            or asset_lifecycle_gate["warnings"]
            or fx_gate["warnings"]
            else "complete"
        ),
    }


def _benchmark_return(benchmark: pd.Series | None, start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    if benchmark is None:
        return None
    usable = benchmark.copy()
    usable.index = pd.to_datetime(usable.index, utc=True).normalize()
    usable = pd.to_numeric(usable.loc[start:end], errors="coerce").dropna()
    if len(usable) < 2 or float(usable.iloc[0]) <= 0:
        return None
    return float(usable.iloc[-1] / usable.iloc[0] - 1)


def _mean_confidence_interval_95(values: pd.Series) -> list[float] | None:
    usable = pd.to_numeric(values, errors="coerce").dropna()
    if len(usable) < 2:
        return None
    mean = float(usable.mean())
    margin = 1.96 * float(usable.std(ddof=1)) / math.sqrt(len(usable))
    return [mean - margin, mean + margin]


def _wilson_interval_95(wins: int, total: int) -> list[float] | None:
    if total <= 0:
        return None
    z = 1.96
    rate = wins / total
    denominator = 1 + z**2 / total
    centre = (rate + z**2 / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(rate * (1 - rate) / total + z**2 / (4 * total**2))
        / denominator
    )
    return [max(0.0, centre - margin), min(1.0, centre + margin)]


def _is_true(value) -> bool:
    return bool(value) if pd.notna(value) else False


def _last_close_or_entry(
    price_lookup: dict,
    symbol: str,
    session: pd.Timestamp,
    entry_price: float,
) -> float:
    value = price_lookup.get((symbol, session), {}).get("close")
    return entry_price if not _valid_positive_number(value) else float(value)


def _position_mark(
    price_lookup: dict,
    symbol: str,
    session: pd.Timestamp,
    position: dict,
    blocked_symbols: set[str],
) -> float:
    if symbol in blocked_symbols:
        return float(
            position.get("last_verified_close", position["entry_execution_price"])
        )
    return _last_close_or_entry(
        price_lookup, symbol, session, position["entry_execution_price"]
    )


def _valid_positive_number(value) -> bool:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(parsed) and parsed > 0


def _valid_ohlc_bar(bar: dict) -> bool:
    if not all(_valid_positive_number(bar.get(column)) for column in ("open", "high", "low", "close")):
        return False
    open_price = float(bar["open"])
    high = float(bar["high"])
    low = float(bar["low"])
    close = float(bar["close"])
    return high >= max(open_price, close) and low <= min(open_price, close)


def _optional_rate(signal: dict, key: str) -> float | None:
    value = signal.get(key)
    if value is None or pd.isna(value):
        return None
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0 or parsed > 0.05:
        raise ValueError(f"{key} must be between 0 and 0.05")
    return parsed


def _execution_cost_profile(
    signal: dict,
    assumptions: ExecutionAssumptions,
    market_impact: MarketImpactAssumptions,
    previous_turnover: float | None,
) -> dict:
    explicit_spread = _optional_rate(signal, "spread_rate")
    explicit_slippage = _optional_rate(signal, "base_slippage_rate")
    explicit_impact = _optional_rate(signal, "impact_rate")
    if (
        explicit_spread is not None
        or explicit_slippage is not None
        or explicit_impact is not None
    ):
        return {
            "profile": "signal_specific_v1",
            "spread_rate": assumptions.spread_rate if explicit_spread is None else explicit_spread,
            "base_slippage_rate": (
                market_impact.base_slippage_rate
                if explicit_slippage is None
                else explicit_slippage
            ),
            "impact_rate": market_impact.impact_rate if explicit_impact is None else explicit_impact,
        }
    if not market_impact.use_turnover_cost_model:
        return {
            "profile": "fixed_cost_v1",
            "spread_rate": assumptions.spread_rate,
            "base_slippage_rate": market_impact.base_slippage_rate,
            "impact_rate": market_impact.impact_rate,
        }
    if previous_turnover is None or not _valid_positive_number(previous_turnover):
        raise ValueError("previous turnover is required for turnover cost model")
    if previous_turnover >= market_impact.high_turnover_threshold:
        spread_rate, base_slippage_rate, tier = 0.0005, 0.00025, "high"
    elif previous_turnover >= market_impact.medium_turnover_threshold:
        spread_rate, base_slippage_rate, tier = 0.0010, 0.00050, "medium"
    elif previous_turnover >= market_impact.low_turnover_threshold:
        spread_rate, base_slippage_rate, tier = 0.0020, 0.00100, "low"
    else:
        raise ValueError("previous turnover is below modeled liquidity tiers")
    return {
        "profile": f"turnover_cost_v1:{tier}",
        "spread_rate": spread_rate,
        "base_slippage_rate": base_slippage_rate,
        "impact_rate": market_impact.impact_rate,
    }


def simulate_ohlc_portfolio(
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    *,
    initial_cash: float = 2_500_000,
    account_name: str = "short_term",
    assumptions: ExecutionAssumptions | None = None,
    market_impact: MarketImpactAssumptions | None = None,
    risk_rules: PortfolioRiskRules | None = None,
    benchmark: pd.Series | None = None,
    benchmarks: dict[str, pd.Series] | None = None,
    input_data_version: str | None = None,
    strategy_version: str | None = None,
    execution_version: str | None = None,
    corporate_actions: pd.DataFrame | None = None,
    corporate_action_coverage: pd.DataFrame | None = None,
    corporate_action_policy: CorporateActionPolicy | None = None,
    asset_lifecycle: pd.DataFrame | None = None,
    asset_universe_coverage: pd.DataFrame | None = None,
    asset_lifecycle_policy: AssetLifecyclePolicy | None = None,
    fx_rates: pd.DataFrame | None = None,
    fx_accounting_policy: FxAccountingPolicy | None = None,
    tax_accounting_policy: TaxAccountingPolicy | None = None,
) -> dict:
    """Simulate long-only daily OHLC signals with conservative execution.

    Signals are decided on ``signal_date`` and may enter only on a later
    ``entry_date``. Entry sizing uses the previous session's volume so the
    entry day's completed volume never leaks into the decision.
    """

    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    assumptions = assumptions or ExecutionAssumptions()
    market_impact = market_impact or MarketImpactAssumptions()
    risk_rules = risk_rules or PortfolioRiskRules()
    corporate_action_policy = corporate_action_policy or CorporateActionPolicy()
    asset_lifecycle_policy = asset_lifecycle_policy or AssetLifecyclePolicy()
    fx_accounting_policy = fx_accounting_policy or FxAccountingPolicy()
    tax_accounting_policy = tax_accounting_policy or TaxAccountingPolicy()
    tax_disclosure = tax_accounting_policy.disclosure()
    tax_metadata = {
        "tax_accounting_version": tax_accounting_policy.version,
        "tax_evaluation_basis": tax_accounting_policy.evaluation_basis,
    }
    required_signals = {"signal_date", "entry_date", "symbol", "side"}
    required_prices = {"price_time", "symbol", "open", "high", "low", "close"}
    if not required_signals.issubset(signals.columns) and not signals.empty:
        raise ValueError(f"signals missing columns: {sorted(required_signals - set(signals.columns))}")
    if not required_prices.issubset(prices.columns) and not prices.empty:
        raise ValueError(f"prices missing columns: {sorted(required_prices - set(prices.columns))}")

    signal_frame = signals.copy()
    price_frame = prices.copy()
    if not price_frame.empty and "currency" not in price_frame:
        price_frame["currency"] = "JPY"
    if "currency" in price_frame:
        price_frame["currency"] = price_frame["currency"].fillna("JPY").astype(str).str.upper()
    for frame, column in ((signal_frame, "signal_date"), (signal_frame, "entry_date"), (price_frame, "price_time")):
        if not frame.empty:
            frame[column] = pd.to_datetime(frame[column], utc=True).dt.normalize()
    for column in ("open", "high", "low", "close", "volume"):
        if column in price_frame:
            price_frame[column] = pd.to_numeric(price_frame[column], errors="coerce")
    corporate_action_gate = evaluate_corporate_action_gate(
        price_frame,
        corporate_actions,
        corporate_action_coverage,
        corporate_action_policy,
    )
    asset_lifecycle_gate = evaluate_asset_lifecycle_gate(
        price_frame,
        asset_lifecycle,
        asset_universe_coverage,
        asset_lifecycle_policy,
    )
    lifecycle_frame = asset_lifecycle_gate["records"]
    universe_coverage_frame = asset_lifecycle_gate["coverage"]
    fx_gate = evaluate_fx_gate(price_frame, fx_rates, fx_accounting_policy)
    fx_frame = fx_gate["rates"]
    action_frame = corporate_action_gate["actions"]
    coverage_frame = corporate_action_gate["coverage"]
    explicitly_modeled_symbols = set(
        action_frame.loc[
            action_frame["action_type"].isin(
                {STOCK_SPLIT, REVERSE_SPLIT, CASH_DIVIDEND}
            )
            & action_frame["status"].eq("confirmed"),
            "symbol",
        ].tolist()
    )
    raw_price_unavailable_symbols: set[str] = set()
    for symbol in explicitly_modeled_symbols:
        symbol_rows = price_frame["symbol"] == symbol
        bases = set(price_frame.loc[symbol_rows, "price_basis"].dropna().astype(str)) if "price_basis" in price_frame else set()
        if "raw_ohlcv_with_adjusted" not in bases:
            continue
        raw_columns = ["raw_open", "raw_high", "raw_low", "raw_close"]
        if not all(column in price_frame for column in raw_columns):
            raw_price_unavailable_symbols.add(symbol)
            continue
        usable = price_frame.loc[symbol_rows, raw_columns].notna().all(axis=1)
        if not usable.all():
            raw_price_unavailable_symbols.add(symbol)
            continue
        for column in ("open", "high", "low", "close"):
            price_frame.loc[symbol_rows, column] = pd.to_numeric(
                price_frame.loc[symbol_rows, f"raw_{column}"], errors="coerce"
            )
        if "raw_volume" in price_frame:
            raw_volume = pd.to_numeric(
                price_frame.loc[symbol_rows, "raw_volume"], errors="coerce"
            )
            valid_raw_volume = raw_volume[raw_volume.notna()]
            price_frame.loc[valid_raw_volume.index, "volume"] = valid_raw_volume
    if raw_price_unavailable_symbols:
        corporate_action_gate["warnings"].append(
            "raw_prices_required_for_explicit_corporate_action"
        )
    manifest = build_run_manifest(
        signal_frame,
        price_frame,
        account_name=account_name,
        assumptions={"execution": assumptions, "market_impact": market_impact},
        risk_rules=risk_rules,
        input_data_version=input_data_version,
        **({"strategy_version": strategy_version} if strategy_version else {}),
        **({"execution_version": execution_version} if execution_version else {}),
        corporate_actions=action_frame,
        corporate_action_coverage=coverage_frame,
        corporate_action_policy=corporate_action_policy,
        asset_lifecycle=lifecycle_frame,
        asset_universe_coverage=universe_coverage_frame,
        asset_lifecycle_policy=asset_lifecycle_policy,
        fx_rates=fx_frame,
        fx_accounting_policy=fx_accounting_policy,
        tax_accounting_policy=tax_accounting_policy,
    )
    if signal_frame.empty or price_frame.empty:
        return _empty_result(
            account_name,
            initial_cash,
            manifest,
            assumptions,
            market_impact,
            risk_rules,
            corporate_action_policy,
            corporate_action_gate,
            asset_lifecycle_policy,
            asset_lifecycle_gate,
            fx_accounting_policy,
            fx_gate,
            tax_accounting_policy,
        )

    price_frame = price_frame.sort_values(["symbol", "price_time"]).drop_duplicates(
        ["symbol", "price_time"], keep="last"
    )
    sessions = pd.DatetimeIndex(sorted(price_frame["price_time"].unique()))
    price_lookup = {
        (row.symbol, row.price_time): row._asdict()
        for row in price_frame.itertuples(index=False)
    }
    previous_bar: dict[tuple[str, pd.Timestamp], dict | None] = {}
    for symbol, group in price_frame.groupby("symbol"):
        previous = None
        for row in group.itertuples(index=False):
            previous_bar[(symbol, row.price_time)] = previous
            previous = row._asdict()

    if "score" not in signal_frame:
        signal_frame["score"] = 0.0
    else:
        signal_frame["score"] = pd.to_numeric(signal_frame["score"], errors="coerce").fillna(0)
    signal_frame = signal_frame.sort_values(
        ["entry_date", "score", "symbol"], ascending=[True, False, True]
    ).reset_index(drop=True)

    cash = float(initial_cash)
    realized_pnl = 0.0
    positions: dict[str, dict] = {}
    transactions: list[dict] = []
    rejected: list[dict] = []
    cards: list[dict] = []
    snapshots: list[dict] = []
    high_watermark = float(initial_cash)
    loss_streak = 0
    cooldown_until_index = -1
    risk_halted = False
    dividend_income = 0.0
    pending_dividends: list[dict] = []
    corporate_action_events: list[dict] = []
    asset_lifecycle_events: list[dict] = []
    fx_events: list[dict] = []
    blocked_symbols: set[str] = set()
    lifecycle_blocked_symbols: set[str] = set()
    unverified_symbols = set(corporate_action_gate["unverified_symbols"])

    def reject(signal: dict, reason: str, warning: str) -> None:
        rejected.append(
            {
                "symbol": signal.get("symbol"),
                "signal_date": signal.get("signal_date"),
                "entry_date": signal.get("entry_date"),
                "reason": reason,
            }
        )
        card = decision_card(
            signal,
            status="rejected",
            manifest=manifest,
            outcome_reason=reason,
            quality_warnings=[warning],
            event_at=signal.get("entry_date") or signal.get("signal_date"),
        )
        cards.append(card)

    for session_index, current_date in enumerate(sessions):
        for symbol, position in list(positions.items()):
            lifecycle, lifecycle_covered = lifecycle_record_on(
                lifecycle_frame,
                universe_coverage_frame,
                symbol=symbol,
                session=current_date,
            )
            delisting_reason = None
            if lifecycle_covered and lifecycle is None:
                delisting_reason = "absent_from_complete_asset_universe"
            elif lifecycle is not None:
                delisted_on = lifecycle.get("delisted_on")
                if lifecycle.get("investability_status") == DELISTED or (
                    delisted_on is not None and delisted_on <= current_date
                ):
                    delisting_reason = "confirmed_delisting"
                elif lifecycle.get("investability_status") in {
                    NON_INVESTABLE,
                    SUSPENDED,
                    UNKNOWN,
                }:
                    lifecycle_blocked_symbols.add(symbol)
                    asset_lifecycle_events.append(
                        {
                            "symbol": symbol,
                            "event_date": current_date,
                            "status": "evaluation_deferred",
                            "reason": "asset_not_investable",
                            "lifecycle": lifecycle,
                        }
                    )
                elif lifecycle.get("investability_status") == INVESTABLE:
                    lifecycle_blocked_symbols.discard(symbol)
            if delisting_reason is None:
                continue
            pnl = -float(position["cost"])
            realized_pnl += pnl
            loss_streak += 1
            if loss_streak >= risk_rules.loss_streak_threshold:
                cooldown_until_index = session_index + risk_rules.cooldown_sessions
                loss_streak = 0
            transactions.append(
                {
                    "account": account_name,
                    "date": current_date,
                    "action": "上場廃止評価",
                    "symbol": symbol,
                    "name": position.get("name", symbol),
                    "quantity": position["quantity"],
                    "execution_price": 0.0,
                    "amount": 0.0,
                    "fee": 0.0,
                    "tax": 0.0,
                    **tax_metadata,
                    "realized_pnl": pnl,
                    "trade_return": -1.0,
                    "reason": delisting_reason,
                    "decision_as_of": position["signal_date"],
                    "participation_rate": None,
                    "slippage_rate": None,
                    "spread_rate": None,
                    "execution_cost_profile": asset_lifecycle_policy.version,
                    "previous_turnover": None,
                    "score": position.get("score"),
                    "sector": position.get("sector", "unknown"),
                }
            )
            asset_lifecycle_events.append(
                {
                    "symbol": symbol,
                    "event_date": current_date,
                    "status": "zero_recovery_applied",
                    "reason": delisting_reason,
                    "policy_version": asset_lifecycle_policy.version,
                    "lifecycle": lifecycle,
                }
            )
            cards.append(
                decision_card(
                    position,
                    status="closed",
                    manifest=manifest,
                    entry_price=position["entry_execution_price"],
                    exit_price=0.0,
                    outcome_reason="上場廃止の保守的ゼロ回収評価",
                    quality_warnings=[delisting_reason],
                    event_at=current_date,
                )
            )
            del positions[symbol]
            blocked_symbols.discard(symbol)
            lifecycle_blocked_symbols.discard(symbol)

        session_events = sorted(
            events_on(action_frame, current_date),
            key=lambda event: (
                0
                if event["action_type"] in {STOCK_SPLIT, REVERSE_SPLIT}
                else 1
                if event["action_type"] == CASH_DIVIDEND
                else 2,
                event["action_id"],
            ),
        )
        for event in session_events:
            symbol = event["symbol"]
            position = positions.get(symbol)
            if position is None:
                continue
            action_type = event["action_type"]
            if event.get("announced_at") is None:
                corporate_action_gate["warnings"].append(
                    "corporate_action_announcement_time_missing"
                )
            if event.get("status") != "confirmed":
                blocked_symbols.add(symbol)
                corporate_action_events.append(
                    {
                        **event,
                        "status": "evaluation_deferred",
                        "reason": "corporate_action_not_confirmed",
                    }
                )
                continue
            if action_type in UNSUPPORTED_ACTIONS:
                blocked_symbols.add(symbol)
                corporate_action_events.append(
                    {
                        **event,
                        "status": "evaluation_deferred",
                        "reason": "unsupported_corporate_action",
                    }
                )
                cards.append(
                    decision_card(
                        position,
                        status="valuation_deferred",
                        manifest=manifest,
                        entry_price=position["entry_execution_price"],
                        outcome_reason="未対応の企業行動",
                        quality_warnings=["合併・株式交換等のため評価を継続しません"],
                        event_at=current_date,
                    )
                )
                continue
            if action_type in {STOCK_SPLIT, REVERSE_SPLIT}:
                if symbol in raw_price_unavailable_symbols:
                    blocked_symbols.add(symbol)
                    corporate_action_events.append(
                        {
                            **event,
                            "status": "evaluation_deferred",
                            "reason": "raw_prices_unavailable",
                        }
                    )
                    continue
                ratio = float(event["ratio"])
                old_quantity = int(position["quantity"])
                adjusted_quantity = old_quantity * ratio
                if not math.isclose(adjusted_quantity, round(adjusted_quantity)):
                    blocked_symbols.add(symbol)
                    corporate_action_events.append(
                        {
                            **event,
                            "status": "evaluation_deferred",
                            "reason": "fractional_share_cash_in_lieu_unmodeled",
                        }
                    )
                    continue
                position["quantity"] = int(round(adjusted_quantity))
                for key in (
                    "entry_execution_price",
                    "stop_price",
                    "take_profit_price",
                ):
                    position[key] = float(position[key]) / ratio
                position["reference_quantity"] = position["quantity"]
                action_label = "株式分割" if action_type == STOCK_SPLIT else "株式併合"
                transaction = {
                    "account": account_name,
                    "date": current_date,
                    "action": action_label,
                    "symbol": symbol,
                    "name": position.get("name", symbol),
                    "quantity": position["quantity"],
                    "previous_quantity": old_quantity,
                    "ratio": ratio,
                    "execution_price": None,
                    "amount": 0.0,
                    "fee": 0.0,
                    "tax": 0.0,
                    **tax_metadata,
                    "realized_pnl": 0.0,
                    "reason": f"企業行動反映: {action_type}",
                    "decision_as_of": position["signal_date"],
                }
                transactions.append(transaction)
                corporate_action_events.append({**event, "status": "applied"})
                continue
            if action_type == CASH_DIVIDEND:
                if symbol in raw_price_unavailable_symbols:
                    blocked_symbols.add(symbol)
                    corporate_action_events.append(
                        {
                            **event,
                            "status": "evaluation_deferred",
                            "reason": "raw_prices_unavailable",
                        }
                    )
                    continue
                if event["currency"] not in {
                    corporate_action_policy.account_currency,
                    "USD",
                }:
                    corporate_action_gate["warnings"].append(
                        "foreign_currency_dividend_unmodeled"
                    )
                    corporate_action_events.append(
                        {
                            **event,
                            "status": "evaluation_deferred",
                            "reason": "unsupported_dividend_currency",
                        }
                    )
                    continue
                pending_dividends.append(
                    {
                        **event,
                        "entitled_quantity": int(position["quantity"]),
                        "gross_amount": float(event["cash_per_share"])
                        * int(position["quantity"]),
                    }
                )
                corporate_action_events.append({**event, "status": "receivable_recorded"})

        entries = signal_frame[signal_frame["entry_date"] == current_date]
        for signal in entries.to_dict(orient="records"):
            symbol = str(signal["symbol"])
            bar = price_lookup.get((symbol, current_date))
            lifecycle, lifecycle_covered = lifecycle_record_on(
                lifecycle_frame,
                universe_coverage_frame,
                symbol=symbol,
                session=current_date,
            )
            if not lifecycle_covered:
                existing_warnings = signal.get("quality_warnings", [])
                if not isinstance(existing_warnings, list):
                    existing_warnings = [existing_warnings]
                signal["quality_warnings"] = [
                    *existing_warnings,
                    "過去時点の投資可能銘柄集合を確認できません",
                ]
                if asset_lifecycle_policy.missing_coverage_policy == "reject":
                    reject(
                        signal,
                        "asset_universe_coverage_unverified",
                        "過去時点の銘柄集合が未確認のためエントリーを見送ります",
                    )
                    continue
            elif lifecycle is None:
                reject(
                    signal,
                    "not_in_historical_asset_universe",
                    "当時の完全な銘柄集合に存在しないためエントリーを見送ります",
                )
                continue
            else:
                listed_on = lifecycle.get("listed_on")
                delisted_on = lifecycle.get("delisted_on")
                if (
                    lifecycle.get("investability_status") != INVESTABLE
                    or (listed_on is not None and listed_on > current_date)
                    or (delisted_on is not None and delisted_on <= current_date)
                ):
                    reject(
                        signal,
                        "asset_not_investable_as_of_entry",
                        "当時の投資可能状態を満たさないためエントリーを見送ります",
                    )
                    continue
                if lifecycle.get("sector_33") or lifecycle.get("sector_17"):
                    signal["sector"] = lifecycle.get("sector_33") or lifecycle.get("sector_17")
                signal["market_as_of_entry"] = lifecycle.get("market")
            if symbol in unverified_symbols:
                existing_warnings = signal.get("quality_warnings", [])
                if not isinstance(existing_warnings, list):
                    existing_warnings = [existing_warnings]
                signal["quality_warnings"] = [
                    *existing_warnings,
                    "企業行動の確認範囲が不足しています",
                ]
                if corporate_action_policy.missing_coverage_policy == "reject":
                    reject(
                        signal,
                        "corporate_action_coverage_unverified",
                        "企業行動を確認できない期間のためエントリーを見送ります",
                    )
                    continue
            if signal["side"] != "long":
                reject(signal, "long_only_account", "下方向・方向未確定は空売りしません")
                continue
            if signal["signal_date"] >= signal["entry_date"]:
                reject(signal, "entry_not_after_signal", "判断後のセッションでのみ約定できます")
                continue
            if risk_halted:
                reject(signal, "portfolio_drawdown_halt", "口座ドローダウン上限に到達しました")
                continue
            if session_index <= cooldown_until_index:
                reject(signal, "loss_streak_cooldown", "連続損失後のクールダウン中です")
                continue
            if bar is None or not _valid_positive_number(bar.get("open")):
                reject(signal, "missing_entry_open", "次営業日の始値がありません")
                continue
            asset_currency = str(bar.get("currency") or "JPY").upper()
            entry_fx_mid = fx_mid_on(fx_frame, asset_currency, current_date)
            if entry_fx_mid is None:
                reject(
                    signal,
                    "fx_rate_unavailable_at_entry",
                    f"{asset_currency}/JPYの約定時点レートを確認できません",
                )
                continue
            entry_fx_execution = (
                1.0
                if asset_currency == "JPY"
                else fx_execution_rate(
                    entry_fx_mid, side="buy", policy=fx_accounting_policy
                )
            )
            if ("tradable" in bar and not _is_true(bar.get("tradable"))) or _is_true(
                bar.get("suspended")
            ):
                reject(signal, "not_tradable", "取引停止または取引不能です")
                continue
            if _is_true(bar.get("limit_up")):
                reject(signal, "limit_up_no_fill", "ストップ高で買い約定不能として扱います")
                continue
            if _is_true(bar.get("special_quote")):
                reject(signal, "special_quote_no_fill", "特別気配中は買い約定不能として扱います")
                continue
            if symbol in positions:
                reject(signal, "symbol_already_held", "同一銘柄の重複保有はしません")
                continue
            if len(positions) >= assumptions.maximum_positions:
                reject(signal, "maximum_positions_reached", "同時保有上限に到達しています")
                continue
            if risk_rules.maximum_position_correlation is not None and positions:
                history = price_frame[
                    (price_frame["price_time"] <= signal["signal_date"])
                    & (price_frame["symbol"].isin([symbol, *positions.keys()]))
                ].pivot(index="price_time", columns="symbol", values="close")
                returns = history.tail(
                    risk_rules.correlation_lookback_sessions + 1
                ).pct_change(fill_method=None)
                excessive_pair = None
                for held_symbol in positions:
                    pair = returns[[symbol, held_symbol]].dropna()
                    if len(pair) < risk_rules.minimum_correlation_observations:
                        continue
                    correlation = float(pair[symbol].corr(pair[held_symbol]))
                    if math.isfinite(correlation) and correlation >= risk_rules.maximum_position_correlation:
                        excessive_pair = (held_symbol, correlation)
                        break
                if excessive_pair is not None:
                    reject(
                        signal,
                        "position_correlation_limit",
                        f"保有中の{excessive_pair[0]}との相関{excessive_pair[1]:.2f}が集中上限以上です",
                    )
                    continue

            previous = previous_bar.get((symbol, current_date))
            previous_volume = None if previous is None else previous.get("volume")
            previous_close = None if previous is None else previous.get("close")
            previous_turnover = (
                float(previous_volume) * float(previous_close)
                if _valid_positive_number(previous_volume)
                and _valid_positive_number(previous_close)
                else None
            )
            if previous_turnover is not None and asset_currency != "JPY":
                previous_fx_mid = fx_mid_on(
                    fx_frame, asset_currency, previous.get("price_time")
                )
                if previous_fx_mid is None:
                    if (
                        market_impact.minimum_previous_turnover > 0
                        or market_impact.use_turnover_cost_model
                    ):
                        reject(
                            signal,
                            "previous_fx_rate_unavailable",
                            "前営業日の円換算売買代金に必要な為替レートがありません",
                        )
                        continue
                    previous_turnover = None
                else:
                    previous_turnover *= previous_fx_mid
            if market_impact.require_volume and (
                previous_volume is None
                or not _valid_positive_number(previous_volume)
            ):
                reject(signal, "missing_previous_volume", "未来の出来高を使わず、前営業日の出来高が必要です")
                continue
            if market_impact.minimum_previous_turnover > 0:
                if (
                    previous_volume is None
                    or previous_close is None
                    or not _valid_positive_number(previous_volume)
                    or not _valid_positive_number(previous_close)
                ):
                    reject(signal, "missing_previous_turnover", "前営業日の売買代金を確認できません")
                    continue
                if previous_turnover < market_impact.minimum_previous_turnover:
                    reject(signal, "minimum_turnover_not_met", "前営業日の売買代金が基準未満です")
                    continue

            try:
                cost_profile = _execution_cost_profile(
                    signal,
                    assumptions,
                    market_impact,
                    previous_turnover,
                )
            except ValueError as exc:
                reject(signal, "execution_cost_profile_unavailable", str(exc))
                continue

            sector = str(signal.get("sector") or "unknown")
            stop_loss = float(signal.get("stop_loss", -0.05))
            take_profit = float(signal.get("take_profit", 0.08))
            maximum_holding_days = int(signal.get("maximum_holding_days", 10))
            if stop_loss >= 0 or take_profit <= 0 or maximum_holding_days <= 0:
                reject(signal, "invalid_exit_rules", "損切り・利益確定・保有期限の設定が不正です")
                continue
            horizon_location = min(
                session_index + maximum_holding_days,
                len(sessions) - 1,
            )
            unsupported = known_unsupported_event_in_horizon(
                action_frame,
                symbol=symbol,
                signal_date=signal["signal_date"],
                entry_date=current_date,
                horizon_end=sessions[horizon_location],
            )
            if unsupported is not None:
                reject(
                    signal,
                    "known_unsupported_corporate_action",
                    "保有予定期間内に合併・株式交換等の未対応イベントがあります",
                )
                continue
            sector_cost = sum(
                position["cost"]
                for position in positions.values()
                if position["sector"] == sector
            )
            sector_capacity = initial_cash * risk_rules.maximum_sector_rate - sector_cost
            existing_open_risk = sum(
                position["planned_risk"] for position in positions.values()
            )
            remaining_open_risk = max(
                0.0,
                initial_cash * risk_rules.maximum_total_open_risk_rate
                - existing_open_risk,
            )
            allowed_trade_risk = min(
                initial_cash * risk_rules.maximum_risk_per_trade_rate,
                remaining_open_risk,
            )
            risk_sized_budget = allowed_trade_risk / abs(stop_loss)
            budget = min(
                cash,
                initial_cash * assumptions.maximum_position_rate,
                max(0.0, sector_capacity),
                risk_sized_budget,
            )
            open_price = float(bar["open"])
            desired_quantity = int(
                budget
                // (
                    open_price
                    * entry_fx_execution
                    * assumptions.lot_size
                    * (1 + assumptions.fee_rate + cost_profile["spread_rate"] / 2)
                )
            ) * assumptions.lot_size
            quantity = desired_quantity
            participation = 0.0
            if _valid_positive_number(previous_volume):
                maximum_quantity = int(
                    float(previous_volume)
                    * market_impact.maximum_volume_participation
                    // assumptions.lot_size
                ) * assumptions.lot_size
                if desired_quantity > maximum_quantity:
                    if not market_impact.allow_partial_fill:
                        reject(signal, "liquidity_limit", "前営業日出来高に対して注文量が大きすぎます")
                        continue
                    quantity = maximum_quantity
                participation = quantity / float(previous_volume)
            if quantity <= 0:
                reject(
                    signal,
                    "insufficient_cash_liquidity_or_risk_budget",
                    "売買単位を満たす資金、流動性、または許容損失枠がありません",
                )
                continue

            slippage = (
                cost_profile["base_slippage_rate"]
                + cost_profile["impact_rate"] * participation
            )
            execution_price = open_price * (
                1 + cost_profile["spread_rate"] / 2 + slippage
            )
            native_gross = execution_price * quantity
            gross = native_gross * entry_fx_execution
            fee = gross * assumptions.fee_rate
            cost = gross + fee
            if cost > cash:
                reject(signal, "insufficient_cash", "費用込み必要額が現金を超えました")
                continue
            cash -= cost
            planned_risk = (
                execution_price
                * abs(stop_loss)
                * quantity
                * entry_fx_execution
                + fee
            )
            position = {
                **signal,
                "quantity": quantity,
                "entry_execution_price": execution_price,
                "asset_currency": asset_currency,
                "account_currency": fx_accounting_policy.account_currency,
                "entry_fx_mid": entry_fx_mid,
                "entry_fx_execution_rate": entry_fx_execution,
                "entry_native_notional": native_gross,
                "entry_fx_cost_jpy": native_gross * (entry_fx_execution - entry_fx_mid),
                "entry_fee": fee,
                "cost": cost,
                "sector": sector,
                "stop_price": execution_price * (1 + stop_loss),
                "take_profit_price": execution_price * (1 + take_profit),
                "maximum_holding_days": maximum_holding_days,
                "held_sessions": 0,
                "slippage_rate": slippage,
                "planned_risk": planned_risk,
                "reference_quantity": quantity,
                "spread_rate": cost_profile["spread_rate"],
                "execution_cost_profile": cost_profile["profile"],
                "previous_turnover": previous_turnover,
            }
            positions[symbol] = position
            transactions.append(
                {
                    "account": account_name,
                    "date": current_date,
                    "action": "仮想エントリー",
                    "symbol": symbol,
                    "name": signal.get("name", symbol),
                    "quantity": quantity,
                    "execution_price": execution_price,
                    "asset_currency": asset_currency,
                    "account_currency": fx_accounting_policy.account_currency,
                    "native_amount": native_gross,
                    "fx_mid": entry_fx_mid,
                    "fx_execution_rate": entry_fx_execution,
                    "fx_cost_jpy": native_gross * (entry_fx_execution - entry_fx_mid),
                    "amount": cost,
                    "fee": fee,
                    "tax": 0.0,
                    **tax_metadata,
                    "realized_pnl": 0.0,
                    "reason": " / ".join(signal.get("reasons", [])),
                    "decision_as_of": signal["signal_date"],
                    "participation_rate": participation,
                    "slippage_rate": slippage,
                    "spread_rate": cost_profile["spread_rate"],
                    "execution_cost_profile": cost_profile["profile"],
                    "previous_turnover": previous_turnover,
                }
            )
            card = decision_card(
                {
                    **signal,
                    "reference_quantity": quantity,
                    "planned_risk": planned_risk,
                },
                status="entered",
                manifest=manifest,
                entry_price=execution_price,
                event_at=current_date,
            )
            cards.append(card)

        for symbol, position in list(positions.items()):
            if symbol in blocked_symbols or symbol in lifecycle_blocked_symbols:
                continue
            bar = price_lookup.get((symbol, current_date))
            if bar is None or not _valid_ohlc_bar(bar):
                card = decision_card(
                    position,
                    status="valuation_deferred",
                    manifest=manifest,
                    entry_price=position["entry_execution_price"],
                    outcome_reason="OHLC品質不合格",
                    quality_warnings=["OHLCの欠損・非正値・大小関係異常により決済判定を見送りました"],
                    event_at=current_date,
                )
                cards.append(card)
                continue
            if ("tradable" in bar and not _is_true(bar.get("tradable"))) or _is_true(
                bar.get("suspended")
            ):
                continue
            if _is_true(bar.get("limit_down")):
                card = decision_card(
                    position,
                    status="exit_deferred",
                    manifest=manifest,
                    entry_price=position["entry_execution_price"],
                    outcome_reason="値幅制限により売却不能",
                    quality_warnings=["ストップ安では売却できない保守的な仮定です"],
                    event_at=current_date,
                )
                cards.append(card)
                continue
            if _is_true(bar.get("special_quote")):
                card = decision_card(
                    position,
                    status="exit_deferred",
                    manifest=manifest,
                    entry_price=position["entry_execution_price"],
                    outcome_reason="特別気配により売却不能",
                    quality_warnings=["特別気配中は決済できない保守的な仮定です"],
                    event_at=current_date,
                )
                cards.append(card)
                continue
            position["held_sessions"] += 1
            open_price = float(bar["open"])
            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar["close"])
            stop_price = position["stop_price"]
            take_price = position["take_profit_price"]
            exit_price = None
            exit_reason = None
            if open_price <= stop_price:
                exit_price, exit_reason = open_price, "損切り条件成立（窓開け）"
            elif open_price >= take_price:
                exit_price, exit_reason = open_price, "利益確定条件成立（窓開け）"
            else:
                stop_hit = low <= stop_price
                take_hit = high >= take_price
                if stop_hit:
                    exit_price, exit_reason = stop_price, "損切り条件成立"
                elif take_hit:
                    exit_price, exit_reason = take_price, "利益確定条件成立"
            if exit_reason is None and position["held_sessions"] >= position["maximum_holding_days"]:
                exit_price, exit_reason = close, "最大保有期間到達"
            if exit_reason is None:
                continue
            asset_currency = position.get("asset_currency", "JPY")
            exit_fx_mid = fx_mid_on(fx_frame, asset_currency, current_date)
            if exit_fx_mid is None:
                fx_gate["warnings"].append("fx_rate_missing_at_exit")
                fx_events.append(
                    {
                        "symbol": symbol,
                        "event_date": current_date,
                        "status": "exit_deferred",
                        "reason": "fx_rate_unavailable_at_exit",
                        "asset_currency": asset_currency,
                    }
                )
                cards.append(
                    decision_card(
                        position,
                        status="exit_deferred",
                        manifest=manifest,
                        entry_price=position["entry_execution_price"],
                        outcome_reason="為替レート欠損により円転不能",
                        quality_warnings=["約定時点の為替レートを確認できません"],
                        event_at=current_date,
                    )
                )
                continue
            exit_fx_execution = (
                1.0
                if asset_currency == "JPY"
                else fx_execution_rate(
                    exit_fx_mid, side="sell", policy=fx_accounting_policy
                )
            )
            slippage = position["slippage_rate"]
            execution_price = float(exit_price) * (
                1 - position["spread_rate"] / 2 - slippage
            )
            native_gross = execution_price * position["quantity"]
            gross = native_gross * exit_fx_execution
            fee = gross * assumptions.fee_rate
            pre_tax_pnl = gross - fee - position["cost"]
            tax = (
                max(pre_tax_pnl, 0.0)
                * tax_accounting_policy.capital_gains_tax_rate
            )
            proceeds = gross - fee - tax
            pnl = proceeds - position["cost"]
            asset_price_pnl_jpy = (
                native_gross - position["entry_native_notional"]
            ) * position["entry_fx_mid"]
            fx_pnl_jpy = native_gross * (
                exit_fx_mid - position["entry_fx_mid"]
            )
            fx_conversion_cost_jpy = (
                native_gross * (exit_fx_execution - exit_fx_mid)
                - position["entry_native_notional"]
                * (position["entry_fx_execution_rate"] - position["entry_fx_mid"])
            )
            cash += proceeds
            realized_pnl += pnl
            if pnl < 0:
                loss_streak += 1
                if loss_streak >= risk_rules.loss_streak_threshold:
                    cooldown_until_index = session_index + risk_rules.cooldown_sessions
                    loss_streak = 0
            else:
                loss_streak = 0
            action = (
                "損切り"
                if exit_reason.startswith("損切り")
                else "利益確定"
                if exit_reason.startswith("利益確定")
                else "保有期限決済"
            )
            transactions.append(
                {
                    "account": account_name,
                    "date": current_date,
                    "action": action,
                    "symbol": symbol,
                    "name": position.get("name", symbol),
                    "quantity": position["quantity"],
                    "execution_price": execution_price,
                    "asset_currency": asset_currency,
                    "account_currency": fx_accounting_policy.account_currency,
                    "native_amount": native_gross,
                    "entry_fx_mid": position["entry_fx_mid"],
                    "exit_fx_mid": exit_fx_mid,
                    "exit_fx_execution_rate": exit_fx_execution,
                    "asset_price_pnl_jpy": asset_price_pnl_jpy,
                    "fx_pnl_jpy": fx_pnl_jpy,
                    "fx_conversion_cost_jpy": fx_conversion_cost_jpy,
                    "fees_tax_jpy": -(position["entry_fee"] + fee + tax),
                    "amount": proceeds,
                    "fee": fee,
                    "tax": tax,
                    **tax_metadata,
                    "realized_pnl": pnl,
                    "trade_return": pnl / position["cost"],
                    "reason": exit_reason,
                    "decision_as_of": position["signal_date"],
                    "participation_rate": None,
                    "slippage_rate": slippage,
                    "spread_rate": position["spread_rate"],
                    "execution_cost_profile": position["execution_cost_profile"],
                    "previous_turnover": position["previous_turnover"],
                    "score": position.get("score"),
                    "sector": position.get("sector", "unknown"),
                }
            )
            card = decision_card(
                position,
                status="closed",
                manifest=manifest,
                entry_price=position["entry_execution_price"],
                exit_price=execution_price,
                outcome_reason=exit_reason,
                event_at=current_date,
            )
            cards.append(card)
            del positions[symbol]

        for receivable in list(pending_dividends):
            if receivable["payable_date"] > current_date:
                continue
            gross_dividend = float(receivable["gross_amount"])
            dividend_currency = receivable["currency"]
            dividend_fx_mid = fx_mid_on(fx_frame, dividend_currency, current_date)
            if dividend_fx_mid is None:
                fx_gate["warnings"].append("fx_rate_missing_for_dividend")
                fx_events.append(
                    {
                        "symbol": receivable["symbol"],
                        "event_date": current_date,
                        "status": "payment_deferred",
                        "reason": "fx_rate_unavailable_for_dividend",
                        "asset_currency": dividend_currency,
                    }
                )
                continue
            dividend_fx_execution = (
                1.0
                if dividend_currency == "JPY"
                else fx_execution_rate(
                    dividend_fx_mid, side="sell", policy=fx_accounting_policy
                )
            )
            gross_dividend_jpy = gross_dividend * dividend_fx_execution
            dividend_tax = (
                gross_dividend_jpy * tax_accounting_policy.dividend_tax_rate
            )
            net_dividend = gross_dividend_jpy - dividend_tax
            cash += net_dividend
            realized_pnl += net_dividend
            dividend_income += net_dividend
            transactions.append(
                {
                    "account": account_name,
                    "date": current_date,
                    "action": "現金配当",
                    "symbol": receivable["symbol"],
                    "name": positions.get(receivable["symbol"], {}).get(
                        "name", receivable["symbol"]
                    ),
                    "quantity": receivable["entitled_quantity"],
                    "execution_price": None,
                    "amount": net_dividend,
                    "fee": 0.0,
                    "tax": dividend_tax,
                    **tax_metadata,
                    "realized_pnl": net_dividend,
                    "reason": "権利確定済み現金配当の支払い",
                    "decision_as_of": None,
                    "ex_date": receivable["ex_date"],
                    "record_date": receivable["record_date"],
                    "payable_date": receivable["payable_date"],
                    "cash_per_share": receivable["cash_per_share"],
                    "asset_currency": dividend_currency,
                    "native_amount": gross_dividend,
                    "fx_mid": dividend_fx_mid,
                    "fx_execution_rate": dividend_fx_execution,
                    "fx_cost_jpy": gross_dividend
                    * (dividend_fx_execution - dividend_fx_mid),
                }
            )
            corporate_action_events.append(
                {**receivable, "status": "paid", "credited_at": current_date}
            )
            pending_dividends.remove(receivable)

        market_value = 0.0
        fx_valuation_complete = True
        for symbol, position in positions.items():
            bar = price_lookup.get((symbol, current_date))
            valuation = position["entry_execution_price"]
            if bar is not None and pd.notna(bar.get("close")):
                valuation = float(bar["close"])
            if symbol in blocked_symbols or symbol in lifecycle_blocked_symbols:
                valuation = float(position.get("last_verified_close", position["entry_execution_price"]))
            else:
                position["last_verified_close"] = valuation
            asset_currency = position.get("asset_currency", "JPY")
            valuation_fx_mid = fx_mid_on(fx_frame, asset_currency, current_date)
            if valuation_fx_mid is None:
                fx_valuation_complete = False
                fx_gate["warnings"].append("fx_rate_missing_at_valuation")
                fx_events.append(
                    {
                        "symbol": symbol,
                        "event_date": current_date,
                        "status": "evaluation_deferred",
                        "reason": "fx_rate_unavailable_for_valuation",
                        "asset_currency": asset_currency,
                    }
                )
                continue
            valuation_fx_rate = (
                1.0
                if asset_currency == "JPY"
                else fx_execution_rate(
                    valuation_fx_mid, side="sell", policy=fx_accounting_policy
                )
            )
            position["valuation_fx_mid"] = valuation_fx_mid
            position["valuation_fx_execution_rate"] = valuation_fx_rate
            position["native_market_value"] = valuation * position["quantity"]
            market_value += position["native_market_value"] * valuation_fx_rate
        cost_basis = sum(position["cost"] for position in positions.values())
        equity = cash + market_value if fx_valuation_complete else None
        unrealized_pnl = market_value - cost_basis if fx_valuation_complete else None
        drawdown = None
        if equity is not None:
            high_watermark = max(high_watermark, equity)
            drawdown = equity / high_watermark - 1
            if drawdown <= -risk_rules.maximum_drawdown:
                risk_halted = True
        snapshots.append(
            {
                "date": current_date,
                "cash": cash,
                "market_value": market_value if fx_valuation_complete else None,
                "equity": equity,
                "realized_pnl": realized_pnl,
                "unrealized_pnl": unrealized_pnl,
                "drawdown": drawdown,
                "risk_halted": risk_halted,
                "cooldown_until_index": cooldown_until_index,
                "dividend_income": dividend_income,
                "corporate_action_blocked_positions": len(blocked_symbols),
                "asset_lifecycle_blocked_positions": len(lifecycle_blocked_symbols),
                "fx_valuation_complete": fx_valuation_complete,
            }
        )

    transaction_frame = pd.DataFrame(transactions)
    snapshot_frame = pd.DataFrame(snapshots)
    closed = (
        transaction_frame[
            transaction_frame["action"].isin(
                ["利益確定", "損切り", "保有期限決済", "上場廃止評価"]
            )
        ]
        if not transaction_frame.empty
        else pd.DataFrame()
    )
    final_equity = snapshot_frame.iloc[-1]["equity"]
    total_return = (
        None
        if final_equity is None or pd.isna(final_equity)
        else float(final_equity / initial_cash - 1)
    )
    entry_rows = (
        transaction_frame[transaction_frame["action"] == "仮想エントリー"]
        if not transaction_frame.empty
        else pd.DataFrame()
    )
    benchmark_start = (
        pd.to_datetime(entry_rows["date"], utc=True).min()
        if not entry_rows.empty
        else snapshot_frame.iloc[0]["date"]
    )
    benchmark_inputs = {}
    if benchmark is not None:
        benchmark_inputs["primary"] = benchmark
    benchmark_inputs.update(benchmarks or {})
    benchmark_rows = []
    for benchmark_name, benchmark_values in benchmark_inputs.items():
        compared_return = _benchmark_return(
            benchmark_values, benchmark_start, snapshot_frame.iloc[-1]["date"]
        )
        benchmark_rows.append(
            {
                "benchmark": str(benchmark_name),
                "start": benchmark_start,
                "end": snapshot_frame.iloc[-1]["date"],
                "benchmark_return": compared_return,
                "strategy_return": total_return,
                "excess_return": (
                    None
                    if compared_return is None or total_return is None
                    else total_return - compared_return
                ),
            }
        )
    benchmark_comparisons = pd.DataFrame(benchmark_rows)
    primary_comparison = (
        benchmark_comparisons.iloc[0] if not benchmark_comparisons.empty else None
    )
    benchmark_return = (
        None if primary_comparison is None else primary_comparison["benchmark_return"]
    )
    metrics = {
        "total_return": total_return,
        "maximum_drawdown": (
            None
            if snapshot_frame["drawdown"].dropna().empty
            else float(snapshot_frame["drawdown"].dropna().min())
        ),
        "closed_trades": int(len(closed)),
        "win_rate": float((closed["realized_pnl"] > 0).mean()) if not closed.empty else None,
        "average_trade_return": float(closed["trade_return"].mean()) if not closed.empty else None,
        "win_rate_ci95": (
            _wilson_interval_95(int((closed["realized_pnl"] > 0).sum()), len(closed))
            if not closed.empty
            else None
        ),
        "average_trade_return_ci95": (
            _mean_confidence_interval_95(closed["trade_return"])
            if not closed.empty
            else None
        ),
        "benchmark_return": benchmark_return,
        "asset_price_pnl_jpy": (
            float(pd.to_numeric(closed.get("asset_price_pnl_jpy"), errors="coerce").sum())
            if not closed.empty and "asset_price_pnl_jpy" in closed
            else 0.0
        ),
        "fx_pnl_jpy": (
            float(pd.to_numeric(closed.get("fx_pnl_jpy"), errors="coerce").sum())
            if not closed.empty and "fx_pnl_jpy" in closed
            else 0.0
        ),
        "fx_conversion_cost_jpy": (
            float(pd.to_numeric(closed.get("fx_conversion_cost_jpy"), errors="coerce").sum())
            if not closed.empty and "fx_conversion_cost_jpy" in closed
            else 0.0
        ),
        "excess_return": (
            None
            if benchmark_return is None or total_return is None
            else total_return - benchmark_return
        ),
    }
    all_blocked_symbols = blocked_symbols | lifecycle_blocked_symbols
    position_rows = []
    for symbol, position in positions.items():
        native_mark = _position_mark(
            price_lookup, symbol, sessions[-1], position, all_blocked_symbols
        )
        valuation_fx_mid = fx_mid_on(
            fx_frame, position.get("asset_currency", "JPY"), sessions[-1]
        )
        valuation_fx_rate = (
            None
            if valuation_fx_mid is None
            else 1.0
            if position.get("asset_currency", "JPY") == "JPY"
            else fx_execution_rate(
                valuation_fx_mid, side="sell", policy=fx_accounting_policy
            )
        )
        native_market_value = native_mark * position["quantity"]
        market_value_jpy = (
            None
            if valuation_fx_rate is None
            else native_market_value * valuation_fx_rate
        )
        position_rows.append(
            {
                "symbol": symbol,
                "name": position.get("name", symbol),
                "quantity": position["quantity"],
                "entry_date": position["entry_date"],
                "entry_price": position["entry_execution_price"],
                "asset_currency": position.get("asset_currency", "JPY"),
                "account_currency": fx_accounting_policy.account_currency,
                "native_market_value": native_market_value,
                "valuation_fx_mid": valuation_fx_mid,
                "valuation_fx_execution_rate": valuation_fx_rate,
                "market_value": market_value_jpy,
                "unrealized_pnl": (
                    None if market_value_jpy is None else market_value_jpy - position["cost"]
                ),
                "sector": position["sector"],
                "corporate_action_blocked": symbol in blocked_symbols,
                "asset_lifecycle_blocked": symbol in lifecycle_blocked_symbols,
            }
        )
    position_frame = pd.DataFrame(position_rows)
    decision_card_frame = pd.DataFrame(cards)
    if not decision_card_frame.empty:
        decision_card_frame = decision_card_frame.sort_values("event_at").reset_index(
            drop=True
        )
    return {
        "account_name": account_name,
        "account_currency": fx_accounting_policy.account_currency,
        "initial_cash": float(initial_cash),
        "cash": float(snapshot_frame.iloc[-1]["cash"]),
        "equity": (
            None if pd.isna(snapshot_frame.iloc[-1]["equity"])
            else float(snapshot_frame.iloc[-1]["equity"])
        ),
        "realized_pnl": float(snapshot_frame.iloc[-1]["realized_pnl"]),
        "unrealized_pnl": (
            None if pd.isna(snapshot_frame.iloc[-1]["unrealized_pnl"])
            else float(snapshot_frame.iloc[-1]["unrealized_pnl"])
        ),
        "positions": position_frame,
        "transactions": transaction_frame,
        "snapshots": snapshot_frame,
        "rejected_signals": pd.DataFrame(rejected),
        "decision_cards": decision_card_frame,
        "benchmark_comparisons": benchmark_comparisons,
        "metrics": metrics,
        "manifest": manifest,
        "assumptions": assumptions,
        "market_impact": market_impact,
        "risk_rules": risk_rules,
        "risk_halted": risk_halted,
        "dividend_income": dividend_income,
        "pending_dividends": pd.DataFrame(pending_dividends),
        "corporate_action_events": pd.DataFrame(corporate_action_events),
        "corporate_action_policy": corporate_action_policy,
        "corporate_action_gate": {
            key: value
            for key, value in corporate_action_gate.items()
            if key not in {"actions", "coverage"}
        },
        "asset_lifecycle_events": pd.DataFrame(asset_lifecycle_events),
        "asset_lifecycle_policy": asset_lifecycle_policy,
        "asset_lifecycle_gate": {
            key: value
            for key, value in asset_lifecycle_gate.items()
            if key not in {"records", "coverage"}
        },
        "fx_accounting_policy": fx_accounting_policy,
        "fx_gate": {
            key: value for key, value in fx_gate.items() if key != "rates"
        },
        "fx_events": pd.DataFrame(fx_events),
        "tax_accounting_policy": tax_accounting_policy,
        "tax_summary": tax_disclosure,
        "quality_warnings": list(
            dict.fromkeys(
                corporate_action_gate["warnings"]
                + asset_lifecycle_gate["warnings"]
                + fx_gate["warnings"]
            )
        ),
        "evaluation_status": (
            "incomplete"
            if blocked_symbols
            or lifecycle_blocked_symbols
            or not bool(snapshot_frame.iloc[-1].get("fx_valuation_complete", True))
            or any(
                event.get("status") == "evaluation_deferred"
                for event in [*corporate_action_events, *asset_lifecycle_events, *fx_events]
            )
            else "warning"
            if corporate_action_gate["warnings"]
            or asset_lifecycle_gate["warnings"]
            or fx_gate["warnings"]
            else "complete"
        ),
    }
