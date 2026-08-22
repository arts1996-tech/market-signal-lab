import pandas as pd
import pytest

from app.backtest.fx_accounting import (
    FxAccountingPolicy,
    fx_execution_rate,
    normalize_fx_rates,
)
from app.backtest.ohlc import MarketImpactAssumptions, simulate_ohlc_portfolio
from app.backtest.portfolio import ExecutionAssumptions


def _prices(closes=(100.0, 100.0, 110.0, 110.0)):
    dates = pd.date_range("2026-01-05", periods=len(closes), tz="UTC")
    return pd.DataFrame(
        {
            "price_time": dates,
            "symbol": "US-A",
            "currency": "USD",
            "open": closes,
            "high": [value + 1 for value in closes],
            "low": [value - 1 for value in closes],
            "close": closes,
            "volume": 10_000,
        }
    )


def _rates(values=(100.0, 100.0, 110.0, 110.0)):
    dates = pd.date_range("2026-01-05", periods=len(values), tz="UTC")
    return pd.DataFrame(
        {
            "price_time": dates,
            "pair": "USDJPY",
            "rate": values,
            "available_at": dates,
            "source": "synthetic",
        }
    )


def _signal():
    dates = pd.date_range("2026-01-05", periods=4, tz="UTC")
    return pd.DataFrame(
        [{
            "signal_date": dates[0],
            "entry_date": dates[1],
            "symbol": "US-A",
            "side": "long",
            "stop_loss": -0.05,
            "take_profit": 0.08,
            "maximum_holding_days": 10,
        }]
    )


def _simulate(prices, rates):
    return simulate_ohlc_portfolio(
        _signal(),
        prices,
        initial_cash=100_000,
        assumptions=ExecutionAssumptions(
            fee_rate=0, spread_rate=0, lot_size=1, maximum_position_rate=1
        ),
        market_impact=MarketImpactAssumptions(base_slippage_rate=0, impact_rate=0),
        fx_rates=rates,
        fx_accounting_policy=FxAccountingPolicy(
            fx_spread_rate=0, fx_conversion_cost_rate=0
        ),
    )


def test_usd_trade_separates_native_price_and_fx_contributions_in_jpy():
    result = _simulate(_prices(), _rates())

    entry = result["transactions"].query("action == '仮想エントリー'").iloc[0]
    closed = result["transactions"].query("action == '利益確定'").iloc[0]
    assert entry["asset_currency"] == "USD"
    assert entry["native_amount"] == pytest.approx(200.0)
    assert entry["amount"] == pytest.approx(20_000.0)
    assert closed["entry_fx_mid"] == 100
    assert closed["exit_fx_mid"] == 110
    assert closed["asset_price_pnl_jpy"] == pytest.approx(2_000.0)
    assert closed["fx_pnl_jpy"] == pytest.approx(2_200.0)
    assert closed["realized_pnl"] == pytest.approx(4_200.0)
    assert result["metrics"]["fx_pnl_jpy"] == pytest.approx(2_200.0)


def test_missing_entry_fx_rate_rejects_trade_without_forward_fill():
    rates = _rates().iloc[[0, 2, 3]].copy()
    result = _simulate(_prices(), rates)

    assert result["transactions"].empty
    assert result["rejected_signals"].iloc[0]["reason"] == "fx_rate_unavailable_at_entry"


def test_missing_valuation_fx_rate_marks_equity_unavailable():
    prices = _prices((100.0, 100.0, 100.0, 100.0))
    rates = _rates((100.0, 100.0, 100.0, 100.0)).iloc[:3]
    result = _simulate(prices, rates)

    assert result["equity"] is None
    assert result["unrealized_pnl"] is None
    assert result["evaluation_status"] == "incomplete"
    assert "fx_rate_missing_at_valuation" in result["quality_warnings"]
    assert result["fx_events"].iloc[-1]["reason"] == "fx_rate_unavailable_for_valuation"


def test_fx_spread_and_conversion_cost_are_directionally_conservative():
    policy = FxAccountingPolicy(fx_spread_rate=0.01, fx_conversion_cost_rate=0.002)
    assert fx_execution_rate(100, side="buy", policy=policy) == pytest.approx(100.7)
    assert fx_execution_rate(100, side="sell", policy=policy) == pytest.approx(99.3)


def test_fx_rate_validation_rejects_nonpositive_or_unsupported_pairs():
    with pytest.raises(ValueError, match="positive"):
        normalize_fx_rates(_rates((0, 100, 100, 100)))
    unsupported = _rates().assign(pair="EURJPY")
    with pytest.raises(ValueError, match="USDJPY"):
        normalize_fx_rates(unsupported)
