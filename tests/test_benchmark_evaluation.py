import pandas as pd
import pytest

from app.analysis.market_calendar import exchange_calendar
from app.backtest.benchmark_evaluation import (
    BenchmarkEvaluationPolicy,
    evaluate_validation_benchmarks,
)
from app.backtest.ohlc import MarketImpactAssumptions
from app.backtest.portfolio import ExecutionAssumptions


def _frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DatetimeIndex]:
    sessions = exchange_calendar("XTKS").sessions_in_range(
        "2026-01-01", "2026-03-31"
    )[:20]
    price_rows = []
    for symbol, asset_type, currency, offset in (
        ("10010", "stock", "JPY", 0.0),
        ("13060", "etf", "JPY", 200.0),
        ("USDX", "etf", "USD", 50.0),
    ):
        for index, session in enumerate(sessions):
            open_price = 1_000 + offset + index
            price_rows.append(
                {
                    "symbol": symbol,
                    "asset_type": asset_type,
                    "currency": currency,
                    "price_time": session,
                    "open": open_price,
                    "close": open_price + 2,
                }
            )
    index_rows = []
    for symbol, offset in (("NIKKEI225", 30_000), ("TOPIX", 2_500)):
        for index, session in enumerate(sessions):
            index_rows.append(
                {
                    "symbol": symbol,
                    "price_time": session,
                    "close": offset + index * 10,
                }
            )
    return pd.DataFrame(price_rows), pd.DataFrame(index_rows), sessions


def _evaluate(prices, indexes, sessions):
    return evaluate_validation_benchmarks(
        window=1,
        strategy_return=0.02,
        validation_prices=prices,
        index_prices=indexes,
        period_start=sessions[0],
        period_end=sessions[-1],
        eligible_symbols=["10010", "13060", "USDX"],
        eligible_etf_symbols=["13060", "USDX"],
        assumptions=ExecutionAssumptions(fee_rate=0.001, spread_rate=0.001),
        market_impact=MarketImpactAssumptions(base_slippage_rate=0.0005),
        policy=BenchmarkEvaluationPolicy(),
    )


def test_benchmarks_use_same_jpy_period_and_explicit_cost_treatment():
    prices, indexes, sessions = _frames()

    result = _evaluate(prices, indexes, sessions)
    rows = pd.DataFrame(result["comparisons"]).set_index("benchmark")

    assert set(rows.index) == {
        "NIKKEI225",
        "TOPIX",
        "TARGET_ETF_EQUAL_WEIGHT",
        "ELIGIBLE_UNIVERSE_EQUAL_WEIGHT",
        "CASH_JPY",
    }
    assert rows.loc["NIKKEI225", "status"] == "available_reference_only"
    assert rows.loc["NIKKEI225", "cost_adjusted"] == False  # noqa: E712
    assert pd.isna(rows.loc["NIKKEI225", "net_return"])
    assert rows.loc["TARGET_ETF_EQUAL_WEIGHT", "status"] == "available_cost_adjusted"
    assert rows.loc["TARGET_ETF_EQUAL_WEIGHT", "component_count"] == 1
    assert (
        rows.loc["TARGET_ETF_EQUAL_WEIGHT", "net_return"]
        < rows.loc["TARGET_ETF_EQUAL_WEIGHT", "gross_return"]
    )
    assert rows.loc["ELIGIBLE_UNIVERSE_EQUAL_WEIGHT", "component_count"] == 2
    assert rows.loc["CASH_JPY", "comparison_return"] == 0.0
    assert (rows["currency"] == "JPY").all()
    assert result["input_hash"]


def test_missing_exact_index_endpoint_is_not_filled_or_inferred():
    prices, indexes, sessions = _frames()
    indexes = indexes[
        ~(
            (indexes["symbol"] == "TOPIX")
            & (indexes["price_time"] == sessions[0])
        )
    ]

    result = _evaluate(prices, indexes, sessions)
    rows = pd.DataFrame(result["comparisons"]).set_index("benchmark")

    assert rows.loc["TOPIX", "status"] == "unavailable"
    assert rows.loc["TOPIX", "reason"] == "exact_endpoint_price_missing"
    assert pd.isna(rows.loc["TOPIX", "comparison_return"])


def test_future_prices_do_not_change_a_frozen_benchmark_window():
    prices, indexes, sessions = _frames()
    first = _evaluate(prices, indexes, sessions)
    future = sessions[-1] + pd.Timedelta(days=30)
    indexes = pd.concat(
        [
            indexes,
            pd.DataFrame(
                [{"symbol": "NIKKEI225", "price_time": future, "close": 1.0}]
            ),
        ],
        ignore_index=True,
    )
    repeated = _evaluate(prices, indexes, sessions)

    first_rows = pd.DataFrame(first["comparisons"]).set_index("benchmark")
    repeated_rows = pd.DataFrame(repeated["comparisons"]).set_index("benchmark")
    assert repeated_rows.loc["NIKKEI225", "comparison_return"] == pytest.approx(
        first_rows.loc["NIKKEI225", "comparison_return"]
    )


def test_foreign_currency_hold_is_excluded_without_fx_conversion():
    prices, indexes, sessions = _frames()
    prices = prices[prices["symbol"] == "USDX"].copy()

    result = evaluate_validation_benchmarks(
        window=1,
        strategy_return=0.0,
        validation_prices=prices,
        index_prices=indexes,
        period_start=sessions[0],
        period_end=sessions[-1],
        eligible_symbols=["USDX"],
        eligible_etf_symbols=["USDX"],
        assumptions=ExecutionAssumptions(),
        market_impact=MarketImpactAssumptions(),
    )
    rows = pd.DataFrame(result["comparisons"]).set_index("benchmark")

    assert rows.loc["TARGET_ETF_EQUAL_WEIGHT", "status"] == "unavailable"
    assert rows.loc["ELIGIBLE_UNIVERSE_EQUAL_WEIGHT", "status"] == "unavailable"
    assert "currency_or_exact_endpoint_price_unavailable" in result["warnings"]


def test_unverified_point_in_time_universe_is_not_replaced_with_current_survivors():
    prices, indexes, sessions = _frames()

    result = evaluate_validation_benchmarks(
        window=1,
        strategy_return=0.0,
        validation_prices=prices,
        index_prices=indexes,
        period_start=sessions[0],
        period_end=sessions[-1],
        eligible_symbols=[],
        eligible_etf_symbols=[],
        eligible_universe_status="unverified",
        assumptions=ExecutionAssumptions(),
        market_impact=MarketImpactAssumptions(),
    )
    rows = pd.DataFrame(result["comparisons"]).set_index("benchmark")

    assert rows.loc["TARGET_ETF_EQUAL_WEIGHT", "reason"] == "asset_universe_unverified"
    assert (
        rows.loc["ELIGIBLE_UNIVERSE_EQUAL_WEIGHT", "reason"]
        == "asset_universe_unverified"
    )
