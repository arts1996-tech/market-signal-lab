import pandas as pd
import pytest

from app.backtest.portfolio import ExecutionAssumptions, simulate_long_portfolio
from app.analysis.market_calendar import exchange_calendar


def _sessions(count: int = 8) -> pd.DatetimeIndex:
    return exchange_calendar("XTKS").sessions_in_range("2026-01-01", "2026-02-28")[:count]


def _trade(
    symbol: str,
    signal_date: pd.Timestamp,
    entry_date: pd.Timestamp,
    exit_date: pd.Timestamp,
    entry_price: float = 1_000,
    exit_price: float = 1_100,
    side: str = "long",
) -> dict:
    return {
        "signal_date": signal_date,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "symbol": symbol,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "side": side,
    }


def test_event_order_does_not_reuse_close_proceeds_for_same_day_open():
    sessions = _sessions()
    trades = pd.DataFrame(
        [
            _trade("FIRST", sessions[0], sessions[1], sessions[3]),
            _trade("SECOND", sessions[2], sessions[3], sessions[5]),
        ]
    )
    assumptions = ExecutionAssumptions(maximum_positions=1, maximum_position_rate=0.5)

    result = simulate_long_portfolio(trades, assumptions=assumptions)

    assert set(result["trades"].query("action == 'entry'")["symbol"]) == {"FIRST"}
    assert "maximum_positions_reached" in set(result["rejected_trades"]["reason"])


def test_event_portfolio_reserves_cash_for_overlapping_positions():
    sessions = _sessions()
    trades = pd.DataFrame(
        [
            _trade("A", sessions[0], sessions[1], sessions[4]),
            _trade("B", sessions[0], sessions[1], sessions[5]),
        ]
    )
    assumptions = ExecutionAssumptions(
        fee_rate=0,
        spread_rate=0,
        lot_size=100,
        maximum_positions=2,
        maximum_position_rate=0.6,
    )

    result = simulate_long_portfolio(
        trades,
        initial_cash=1_000_000,
        assumptions=assumptions,
    )

    entries = result["trades"].query("action == 'entry'")
    assert len(entries) == 2
    first_quantity, second_quantity = entries["quantity"].tolist()
    assert first_quantity == 600
    assert second_quantity == 400
    assert result["snapshots"]["cash"].min() == 0
    assert result["cash"] == pytest.approx(1_100_000)


def test_event_portfolio_applies_spread_fees_and_tax_to_positive_trade():
    sessions = _sessions()
    trades = pd.DataFrame([_trade("A", sessions[0], sessions[1], sessions[2])])
    assumptions = ExecutionAssumptions(
        fee_rate=0.001,
        spread_rate=0.002,
        tax_rate=0.20,
        lot_size=100,
        maximum_position_rate=0.5,
    )

    result = simulate_long_portfolio(
        trades,
        initial_cash=1_000_000,
        assumptions=assumptions,
    )

    exit_row = result["trades"].query("action == 'exit'").iloc[0]
    assert exit_row["fee"] > 0
    assert exit_row["tax"] > 0
    assert result["realized_pnl"] == pytest.approx(exit_row["realized_pnl"])
    assert result["cash"] == pytest.approx(1_000_000 + exit_row["realized_pnl"])


def test_event_portfolio_reports_benchmark_and_drawdown_metrics():
    sessions = _sessions()
    losing_trade = pd.DataFrame(
        [_trade("A", sessions[0], sessions[1], sessions[4], exit_price=900)]
    )
    benchmark = pd.Series([100, 105, 110], index=sessions[:3])

    result = simulate_long_portfolio(losing_trade, benchmark=benchmark)

    metrics = result["metrics"]
    assert metrics["closed_trades"] == 1
    assert metrics["win_rate"] == 0
    assert metrics["maximum_drawdown"] < 0
    # The benchmark is aligned to the portfolio's first entry date, so the
    # pre-entry move from the signal date is not credited to the comparison.
    assert metrics["benchmark_return"] == pytest.approx(110 / 105 - 1)
    assert metrics["excess_return"] < 0


def test_event_portfolio_rejects_same_day_signal_and_entry():
    sessions = _sessions()
    invalid = pd.DataFrame([_trade("A", sessions[1], sessions[1], sessions[2])])

    result = simulate_long_portfolio(invalid)

    assert result["trades"].empty
    assert set(result["rejected_trades"]["reason"]) == {"entry_not_after_signal"}
