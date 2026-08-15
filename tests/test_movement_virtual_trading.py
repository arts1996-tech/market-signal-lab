import pandas as pd

from app.analysis.market_calendar import exchange_calendar
from app.analysis.movement_candidates import apply_virtual_trade_feedback, build_movement_candidates
from app.analysis.virtual_trading import (
    build_virtual_trades,
    generate_demo_phase4_data,
    simulate_virtual_account,
    virtual_signals_from_reference_trades,
    summarize_virtual_trade_feedback,
)


def _price_rows(symbol: str, values: list[float], name: str | None = None, source: str = "jquants") -> list[dict]:
    dates = exchange_calendar("XTKS").sessions_in_range("2026-01-01", "2026-12-31")[
        : len(values)
    ]
    return [
        {
            "symbol": symbol,
            "name": name or symbol,
            "price_time": date,
            "open": value,
            "close": value,
            "source": source,
            "fetched_at": date,
        }
        for date, value in zip(dates, values, strict=True)
    ]


def test_build_movement_candidates_returns_insufficient_rows():
    japan_prices = pd.DataFrame(_price_rows("86970", [100, 101, 102]))

    result = build_movement_candidates(pd.DataFrame(), japan_prices, min_observations=30)

    assert result["candidates"].empty
    assert result["insufficient"].loc[0, "symbol"] == "86970"


def test_movement_candidates_reject_non_contiguous_thirty_observations():
    old_dates = exchange_calendar("XTKS").sessions_in_range("2024-01-01", "2024-03-31")[:20]
    recent_dates = exchange_calendar("XTKS").sessions_in_range("2026-04-01", "2026-05-31")[:15]
    dates = old_dates.append(recent_dates)
    japan_prices = pd.DataFrame(
        {
            "symbol": "86970",
            "name": "JPX",
            "price_time": dates,
            "close": range(100, 135),
        }
    )

    result = build_movement_candidates(pd.DataFrame(), japan_prices, min_observations=30)

    assert result["candidates"].empty
    assert result["eligible_count"] == 0
    assert result["insufficient"].loc[0, "observations"] == 15
    assert result["insufficient"].loc[0, "total_observations"] == 35


def test_virtual_trade_feedback_is_informational_and_does_not_adjust_score():
    adjusted, reason, feedback_score = apply_virtual_trade_feedback(
        70,
        {"trades": 5, "win_rate": 0.8, "average_return": 0.03, "large_move_rate": 0.5},
    )

    assert adjusted == 70
    assert "スコア未反映" in reason
    assert feedback_score is None


def test_virtual_trade_feedback_loop_builds_summary():
    index_prices = pd.DataFrame(
        _price_rows("NASDAQCOM", [100 + i for i in range(80)], source="fred")
        + _price_rows("DJIA", [200 + i for i in range(80)], source="fred")
        + _price_rows("SP500", [300 + i for i in range(80)], source="fred")
        + _price_rows("NIKKEI225", [400 + i for i in range(80)], source="fred")
    )
    japan_prices = pd.DataFrame(_price_rows("86970", [100 + i * 1.8 for i in range(80)]))

    trades = build_virtual_trades(
        index_prices,
        japan_prices,
        score_threshold=50,
        holding_days=5,
        min_observations=30,
    )
    feedback = summarize_virtual_trade_feedback(trades)

    assert not trades.empty
    assert "86970" in feedback
    assert feedback["86970"]["trades"] > 0


def test_virtual_trade_enters_at_next_session_open_without_same_close_lookahead():
    index_prices = pd.DataFrame(
        _price_rows("NASDAQCOM", [100 + i for i in range(80)], source="fred")
        + _price_rows("NIKKEI225", [200 + i for i in range(80)], source="fred")
    )
    rows = _price_rows("86970", [100 + i for i in range(80)])
    for index, row in enumerate(rows):
        row["open"] = 1000 + index
    japan_prices = pd.DataFrame(rows)

    trades = build_virtual_trades(
        index_prices,
        japan_prices,
        score_threshold=0,
        holding_days=5,
        min_observations=30,
    )

    assert not trades.empty
    assert (trades["signal_date"] < trades["entry_date"]).all()
    expected = japan_prices.copy()
    expected["price_time"] = pd.to_datetime(expected["price_time"], utc=True).dt.normalize()
    expected_open = expected.set_index("price_time")["open"]
    for trade in trades.itertuples(index=False):
        assert trade.entry_price == expected_open.loc[trade.entry_date]
        assert trade.entry_price_rule == "next_xtks_session_open"


def test_virtual_trade_skips_when_next_session_open_is_missing():
    japan_prices = pd.DataFrame(_price_rows("86970", [100 + i for i in range(40)]))
    japan_prices["open"] = float("nan")

    trades = build_virtual_trades(
        pd.DataFrame(),
        japan_prices,
        score_threshold=0,
        holding_days=5,
        min_observations=30,
    )

    assert trades.empty


def test_virtual_trades_require_thirty_observations_in_live_like_input():
    index_prices = pd.DataFrame(
        _price_rows("NASDAQCOM", [100 + i for i in range(40)], source="fred")
        + _price_rows("NIKKEI225", [200 + i for i in range(40)], source="fred")
    )
    japan_prices = pd.DataFrame(_price_rows("86970", [100 + i for i in range(29)]))

    trades = build_virtual_trades(index_prices, japan_prices, score_threshold=0, holding_days=5)

    assert trades.empty


def test_virtual_trades_do_not_cross_a_session_gap():
    old_dates = exchange_calendar("XTKS").sessions_in_range("2024-01-01", "2024-03-31")[:25]
    recent_dates = exchange_calendar("XTKS").sessions_in_range("2026-04-01", "2026-05-31")[:15]
    dates = old_dates.append(recent_dates)
    japan_prices = pd.DataFrame(
        {
            "symbol": "86970",
            "name": "JPX",
            "price_time": dates,
            "close": range(100, 140),
        }
    )

    trades = build_virtual_trades(
        pd.DataFrame(),
        japan_prices,
        score_threshold=0,
        holding_days=5,
        min_observations=30,
    )

    assert trades.empty


def test_demo_phase4_data_is_explicitly_isolated_and_supports_two_accounts():
    index_prices, japan_prices = generate_demo_phase4_data()

    assert set(index_prices["source"]) == {"demo"}
    assert set(japan_prices["source"]) == {"demo"}
    short_trades = build_virtual_trades(index_prices, japan_prices, score_threshold=40, holding_days=5)
    mid_trades = build_virtual_trades(index_prices, japan_prices, score_threshold=40, holding_days=20)

    short = simulate_virtual_account(
        short_trades, account_name="short_term", price_history=japan_prices
    )
    mid = simulate_virtual_account(mid_trades, account_name="mid_term", price_history=japan_prices)

    assert short["initial_cash"] == 2_500_000
    assert mid["initial_cash"] == 2_500_000
    assert short["account_name"] != mid["account_name"]
    assert short["cash"] >= 0
    assert mid["cash"] >= 0
    if not short["trades"].empty:
        assert set(short["trades"]["account_name"]) == {"short_term"}


def test_virtual_account_reserves_cash_and_rejects_short_equivalent_trades():
    sessions = exchange_calendar("XTKS").sessions_in_range("2026-01-01", "2026-01-31")
    trades = pd.DataFrame(
        [
            {
                "signal_date": sessions[0],
                "entry_date": sessions[1],
                "exit_date": sessions[4],
                "symbol": "LONG",
                "score": 80,
                "side": "long",
                "entry_price": 1_000,
                "exit_price": 1_100,
            },
            {
                "signal_date": sessions[0],
                "entry_date": sessions[1],
                "exit_date": sessions[4],
                "symbol": "DOWN",
                "score": 90,
                "side": "observe_only",
                "entry_price": 1_000,
                "exit_price": 900,
            },
        ]
    )

    account = simulate_virtual_account(
        trades,
        initial_cash=1_000_000,
        allocation_rate=0.50,
        lot_size=100,
    )

    entries = account["trades"].query("action == 'entry'")
    assert set(entries["symbol"]) == {"LONG"}
    assert account["cash"] > 1_000_000
    assert set(account["rejected_trades"]["reason"]) == {"long_only_account"}
    assert account["metrics"]["closed_trades"] == 1


def test_reference_trade_adapter_never_passes_future_outcomes_to_engine():
    sessions = exchange_calendar("XTKS").sessions_in_range("2026-01-01", "2026-01-31")
    trades = pd.DataFrame(
        [
            {
                "signal_date": sessions[0],
                "entry_date": sessions[1],
                "exit_date": sessions[5],
                "symbol": "SAFE",
                "name": "Safe signal",
                "score": 80,
                "side": "long",
                "entry_price": 100,
                "exit_price": 999,
                "return": 8.99,
                "outcome": "future result",
                "entry_reasons": ["known at signal"],
                "outcome_reasons": ["future-only explanation"],
                "holding_days": 5,
            }
        ]
    )

    signals = virtual_signals_from_reference_trades(trades)

    assert len(signals) == 1
    assert "exit_date" not in signals
    assert "exit_price" not in signals
    assert "return" not in signals
    assert "outcome" not in signals
    assert "outcome_reasons" not in signals
    assert signals.iloc[0]["reasons"] == ["known at signal"]
