"""Point-in-time benchmark comparisons for real walk-forward validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from app.backtest.audit import frame_hash, stable_payload_hash
from app.backtest.ohlc import MarketImpactAssumptions
from app.backtest.portfolio import ExecutionAssumptions


BENCHMARK_EVALUATION_VERSION = "walk-forward-benchmarks-v1"


@dataclass(frozen=True)
class BenchmarkEvaluationPolicy:
    """Rules for comparable, JPY-denominated validation benchmarks."""

    version: str = BENCHMARK_EVALUATION_VERSION
    account_currency: str = "JPY"
    index_symbols: tuple[str, ...] = ("NIKKEI225", "TOPIX")


def empty_benchmark_evaluation(
    policy: BenchmarkEvaluationPolicy,
    assumptions: ExecutionAssumptions,
    market_impact: MarketImpactAssumptions,
) -> dict:
    return {
        "version": policy.version,
        "policy": asdict(policy),
        "execution_costs": {
            "fee_rate_per_side": assumptions.fee_rate,
            "spread_rate_round_trip": assumptions.spread_rate,
            "base_slippage_rate_per_side": market_impact.base_slippage_rate,
            "slippage_multiplier": market_impact.slippage_multiplier,
            "tax_rate": assumptions.tax_rate,
        },
        "comparison_period_basis": "registered_validation_window_start_to_end",
        "index_cost_treatment": (
            "non_tradable_reference_only; no execution cost is invented"
        ),
        "windows": [],
        "input_hash": stable_payload_hash([]),
        "warnings": ["no_completed_validation_windows"],
    }


def _normalize_prices(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    result = frame.copy()
    result["price_time"] = pd.to_datetime(
        result["price_time"], utc=True, errors="coerce"
    ).dt.normalize()
    return result[result["price_time"].notna()].copy()


def _value_at(
    frame: pd.DataFrame,
    symbol: str,
    when: pd.Timestamp,
    column: str,
) -> float | None:
    if frame.empty or column not in frame:
        return None
    rows = frame[
        (frame["symbol"].astype(str) == str(symbol))
        & (frame["price_time"] == when)
    ]
    if rows.empty:
        return None
    values = pd.to_numeric(rows[column], errors="coerce").dropna()
    if values.empty or float(values.iloc[-1]) <= 0:
        return None
    return float(values.iloc[-1])


def _net_hold_return(
    entry_price: float,
    exit_price: float,
    assumptions: ExecutionAssumptions,
    market_impact: MarketImpactAssumptions,
) -> float:
    half_spread = assumptions.spread_rate / 2
    slippage = (
        market_impact.base_slippage_rate * market_impact.slippage_multiplier
    )
    entry_execution = entry_price * (
        1 + half_spread + slippage
    )
    exit_execution = exit_price * (
        1 - half_spread - slippage
    )
    entry_cost = entry_execution * (1 + assumptions.fee_rate)
    exit_proceeds = exit_execution * (1 - assumptions.fee_rate)
    return float(exit_proceeds / entry_cost - 1)


def _unavailable_row(
    *,
    window: int,
    benchmark: str,
    label: str,
    benchmark_type: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    currency: str,
    reason: str,
    strategy_return: float,
) -> dict:
    return {
        "window": window,
        "benchmark": benchmark,
        "label": label,
        "benchmark_type": benchmark_type,
        "status": "unavailable",
        "reason": reason,
        "currency": currency,
        "period_start": start,
        "period_end": end,
        "gross_return": None,
        "net_return": None,
        "comparison_return": None,
        "cost_adjusted": False,
        "modeled_cost_rate": None,
        "strategy_return": strategy_return,
        "excess_return": None,
        "component_count": 0,
        "pricing_basis": None,
    }


def _equal_weight_hold(
    frame: pd.DataFrame,
    symbols: list[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    account_currency: str,
    assumptions: ExecutionAssumptions,
    market_impact: MarketImpactAssumptions,
) -> tuple[float | None, float | None, int, list[str]]:
    gross_returns: list[float] = []
    net_returns: list[float] = []
    rejected: list[str] = []
    for symbol in sorted(set(str(value) for value in symbols)):
        symbol_rows = frame[frame["symbol"].astype(str) == symbol]
        currencies = (
            set(symbol_rows["currency"].dropna().astype(str))
            if "currency" in symbol_rows
            else set()
        )
        if currencies != {account_currency}:
            rejected.append(f"{symbol}:currency_unavailable_or_mismatch")
            continue
        entry_price = _value_at(frame, symbol, start, "open")
        exit_price = _value_at(frame, symbol, end, "close")
        if entry_price is None or exit_price is None:
            rejected.append(f"{symbol}:exact_endpoint_price_missing")
            continue
        gross_returns.append(float(exit_price / entry_price - 1))
        net_returns.append(
            _net_hold_return(entry_price, exit_price, assumptions, market_impact)
        )
    if not net_returns:
        return None, None, 0, rejected
    return (
        float(sum(gross_returns) / len(gross_returns)),
        float(sum(net_returns) / len(net_returns)),
        len(net_returns),
        rejected,
    )


def evaluate_validation_benchmarks(
    *,
    window: int,
    strategy_return: float,
    validation_prices: pd.DataFrame,
    index_prices: pd.DataFrame,
    period_start: pd.Timestamp,
    period_end: pd.Timestamp,
    eligible_symbols: list[str],
    eligible_etf_symbols: list[str],
    eligible_universe_status: str = "provided",
    assumptions: ExecutionAssumptions,
    market_impact: MarketImpactAssumptions,
    policy: BenchmarkEvaluationPolicy | None = None,
) -> dict:
    """Compare one frozen validation window without filling missing prices.

    Tradable holds use explicit fee, spread, and base-slippage assumptions.
    Indices remain raw, non-tradable references so the implementation does not
    invent the cost of an unspecified index product.
    """

    policy = policy or BenchmarkEvaluationPolicy()
    start = pd.Timestamp(period_start)
    end = pd.Timestamp(period_end)
    start = (
        start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    )
    end = end.tz_localize("UTC") if end.tzinfo is None else end.tz_convert("UTC")
    start = start.normalize()
    end = end.normalize()
    prices = _normalize_prices(validation_prices)
    indexes = _normalize_prices(index_prices)
    if not indexes.empty:
        indexes = indexes[
            (indexes["price_time"] >= start) & (indexes["price_time"] <= end)
        ]
    rows: list[dict] = []

    index_labels = {"NIKKEI225": "日経平均", "TOPIX": "TOPIX"}
    for symbol in policy.index_symbols:
        label = index_labels.get(symbol, symbol)
        start_value = _value_at(indexes, symbol, start, "close")
        end_value = _value_at(indexes, symbol, end, "close")
        if start_value is None or end_value is None:
            rows.append(
                _unavailable_row(
                    window=window,
                    benchmark=symbol,
                    label=label,
                    benchmark_type="non_tradable_index",
                    start=start,
                    end=end,
                    currency=policy.account_currency,
                    reason="exact_endpoint_price_missing",
                    strategy_return=strategy_return,
                )
            )
            continue
        gross_return = float(end_value / start_value - 1)
        rows.append(
            {
                "window": window,
                "benchmark": symbol,
                "label": label,
                "benchmark_type": "non_tradable_index",
                "status": "available_reference_only",
                "reason": "non_tradable_index_cost_not_inferred",
                "currency": policy.account_currency,
                "period_start": start,
                "period_end": end,
                "gross_return": gross_return,
                "net_return": None,
                "comparison_return": gross_return,
                "cost_adjusted": False,
                "modeled_cost_rate": None,
                "strategy_return": strategy_return,
                "excess_return": float(strategy_return - gross_return),
                "component_count": 1,
                "pricing_basis": "exact_close_to_close_reference",
            }
        )

    hold_specs = (
        (
            "TARGET_ETF_EQUAL_WEIGHT",
            "対象ETFの単純保有",
            "tradable_target_etf_hold",
            eligible_etf_symbols,
            "no_eligible_target_etf",
        ),
        (
            "ELIGIBLE_UNIVERSE_EQUAL_WEIGHT",
            "対象銘柄の単純保有",
            "tradable_equal_weight_hold",
            eligible_symbols,
            "no_eligible_symbols",
        ),
    )
    for benchmark, label, benchmark_type, symbols, empty_reason in hold_specs:
        if not symbols:
            reason = (
                "asset_universe_unverified"
                if eligible_universe_status == "unverified"
                else empty_reason
            )
            rows.append(
                _unavailable_row(
                    window=window,
                    benchmark=benchmark,
                    label=label,
                    benchmark_type=benchmark_type,
                    start=start,
                    end=end,
                    currency=policy.account_currency,
                    reason=reason,
                    strategy_return=strategy_return,
                )
            )
            continue
        gross_return, net_return, component_count, rejected = _equal_weight_hold(
            prices,
            symbols,
            start,
            end,
            account_currency=policy.account_currency,
            assumptions=assumptions,
            market_impact=market_impact,
        )
        if net_return is None:
            reason = (
                "currency_or_exact_endpoint_price_unavailable"
                if rejected
                else empty_reason
            )
            rows.append(
                _unavailable_row(
                    window=window,
                    benchmark=benchmark,
                    label=label,
                    benchmark_type=benchmark_type,
                    start=start,
                    end=end,
                    currency=policy.account_currency,
                    reason=reason,
                    strategy_return=strategy_return,
                )
            )
            continue
        rows.append(
            {
                "window": window,
                "benchmark": benchmark,
                "label": label,
                "benchmark_type": benchmark_type,
                "status": "available_cost_adjusted",
                "reason": (
                    None
                    if not rejected
                    else "excluded_components:"
                    + ",".join(
                        sorted({item.split(":", 1)[-1] for item in rejected})
                    )
                ),
                "currency": policy.account_currency,
                "period_start": start,
                "period_end": end,
                "gross_return": gross_return,
                "net_return": net_return,
                "comparison_return": net_return,
                "cost_adjusted": True,
                "modeled_cost_rate": float(gross_return - net_return),
                "strategy_return": strategy_return,
                "excess_return": float(strategy_return - net_return),
                "component_count": component_count,
                "eligible_universe_status": eligible_universe_status,
                "rejected_component_count": len(rejected),
                "pricing_basis": (
                    "equal_weight_fractional_proxy_exact_open_to_close; "
                    "fee_spread_base_slippage_both_sides"
                ),
            }
        )

    rows.append(
        {
            "window": window,
            "benchmark": "CASH_JPY",
            "label": "現金保有",
            "benchmark_type": "cash_hold",
            "status": "available_cost_adjusted",
            "reason": None,
            "currency": policy.account_currency,
            "period_start": start,
            "period_end": end,
            "gross_return": 0.0,
            "net_return": 0.0,
            "comparison_return": 0.0,
            "cost_adjusted": True,
            "modeled_cost_rate": 0.0,
            "strategy_return": strategy_return,
            "excess_return": strategy_return,
            "component_count": 1,
            "pricing_basis": "cash_balance_no_trade",
        }
    )
    input_frame = pd.concat([prices, indexes], ignore_index=True, sort=False)
    return {
        "version": policy.version,
        "policy": asdict(policy),
        "window": window,
        "period_start": start,
        "period_end": end,
        "comparisons": rows,
        "input_hash": frame_hash(input_frame),
        "warnings": sorted(
            {
                str(row["reason"])
                for row in rows
                if row["status"] == "unavailable" and row.get("reason")
            }
        ),
    }
