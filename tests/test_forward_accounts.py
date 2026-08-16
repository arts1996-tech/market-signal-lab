import pandas as pd
import pytest

from app.analysis.market_calendar import exchange_calendar
from app.backtest.forward_account import advance_forward_accounts_as_of
from app.backtest.ohlc import MarketImpactAssumptions, PortfolioRiskRules
from app.backtest.portfolio import ExecutionAssumptions
from app.services.forward_account_ledger import (
    build_virtual_account_daily_state,
    build_virtual_account_events,
    persist_forward_accounts,
)


def _sessions(count: int = 5) -> pd.DatetimeIndex:
    return exchange_calendar("XTKS").sessions_in_range("2026-01-01", "2026-03-31")[
        :count
    ]


def _prices() -> pd.DataFrame:
    dates = _sessions()
    return pd.DataFrame(
        {
            "price_time": dates,
            "symbol": "A",
            "open": [100.0] * len(dates),
            "high": [101.0, 101.0, 110.0, 101.0, 101.0],
            "low": [99.0] * len(dates),
            "close": [100.0] * len(dates),
            "volume": [1_000_000.0] * len(dates),
            "source": "demo",
        }
    )


def _signal() -> pd.DataFrame:
    dates = _sessions()
    return pd.DataFrame(
        [
            {
                "signal_date": dates[0],
                "entry_date": dates[1],
                "symbol": "A",
                "name": "A",
                "sector": "technology",
                "score": 80,
                "side": "long",
                "minimum_score": 70,
                "reasons": ["point-in-time test signal"],
            }
        ]
    )


def _execution() -> tuple[
    ExecutionAssumptions,
    MarketImpactAssumptions,
    PortfolioRiskRules,
]:
    return (
        ExecutionAssumptions(
            fee_rate=0,
            spread_rate=0,
            tax_rate=0,
            lot_size=1,
            maximum_positions=2,
            maximum_position_rate=0.30,
        ),
        MarketImpactAssumptions(
            require_volume=False,
            base_slippage_rate=0,
            impact_rate=0,
        ),
        PortfolioRiskRules(maximum_sector_rate=1),
    )


def _advance(signals, prices, as_of, previous_states=None):
    assumptions, market_impact, risk_rules = _execution()
    return advance_forward_accounts_as_of(
        signals,
        prices,
        as_of=as_of,
        previous_states=previous_states,
        assumptions=assumptions,
        market_impact=market_impact,
        risk_rules=risk_rules,
    )


def test_forward_accounts_start_with_independent_jpy_2_5m_balances():
    result = _advance(pd.DataFrame(), _prices(), _sessions()[0])

    assert set(result["accounts"]) == {"short_term", "mid_term"}
    assert result["initial_cash_each"] == 2_500_000
    assert result["transfer_between_accounts"] is False
    for account_name, account in result["accounts"].items():
        assert account["account_name"] == account_name
        assert account["initial_cash"] == 2_500_000
        assert account["cash"] == 2_500_000
        assert account["equity"] == 2_500_000


def test_forward_accounts_carry_orders_positions_and_balances_to_next_session():
    dates = _sessions()
    prices = _prices()
    first = _advance(_signal(), prices, dates[0])

    assert all(
        len(account["pending_orders"]) == 1
        for account in first["accounts"].values()
    )

    second = _advance(
        pd.DataFrame(),
        prices,
        dates[1],
        previous_states=first["accounts"],
    )

    for account in second["accounts"].values():
        assert len(account["positions"]) == 1
        assert account["cash"] < 2_500_000
        assert account["carry_forward"]["cash"] == account["cash"]
        assert account["carry_forward"]["realized_pnl"] == 0
        assert account["last_market_session"] == pd.Timestamp(dates[1], tz="UTC")


def test_short_and_mid_accounts_apply_separate_exit_rules_without_transfers():
    dates = _sessions()
    prices = _prices()
    first = _advance(_signal(), prices, dates[0])
    second = _advance(
        pd.DataFrame(), prices, dates[1], previous_states=first["accounts"]
    )
    third = _advance(
        pd.DataFrame(), prices, dates[2], previous_states=second["accounts"]
    )

    short = third["accounts"]["short_term"]
    mid = third["accounts"]["mid_term"]
    assert short["strategy_version"] == "forward-short-term-v1"
    assert mid["strategy_version"] == "forward-mid-term-v1"
    assert short["account_rule"]["take_profit"] == 0.08
    assert mid["account_rule"]["take_profit"] == 0.18
    assert short["positions"].empty
    assert len(mid["positions"]) == 1
    assert "利益確定" in set(short["transactions"]["action"])
    assert "利益確定" not in set(mid["transactions"]["action"])
    assert short["realized_pnl"] > 0
    assert mid["realized_pnl"] == 0
    assert short["cumulative_pnl"] == pytest.approx(
        short["equity"] - short["initial_cash"]
    )
    assert mid["cumulative_pnl"] == pytest.approx(
        mid["equity"] - mid["initial_cash"]
    )


def test_forward_account_retry_with_same_signal_is_idempotent():
    dates = _sessions()
    first = _advance(_signal(), _prices(), dates[0])

    repeated = _advance(
        _signal(),
        _prices(),
        dates[0],
        previous_states=first["accounts"],
    )

    for account in repeated["accounts"].values():
        assert len(account["signal_history"]) == 1
        assert len(account["pending_orders"]) == 1
        assert account["cash"] == 2_500_000


def test_forward_account_rejects_strategy_or_time_rewind():
    dates = _sessions()
    first = _advance(_signal(), _prices(), dates[1])
    changed = {name: dict(state) for name, state in first["accounts"].items()}
    changed["short_term"]["strategy_version"] = "changed-strategy"

    with pytest.raises(ValueError, match="different strategy"):
        _advance(
            pd.DataFrame(),
            _prices(),
            dates[2],
            previous_states=changed,
        )

    with pytest.raises(ValueError, match="backwards"):
        _advance(
            pd.DataFrame(),
            _prices(),
            dates[0],
            previous_states=first["accounts"],
        )


def test_forward_account_excludes_future_prices_from_state():
    dates = _sessions()

    result = _advance(_signal(), _prices(), dates[1])

    known = result["known_prices"]
    assert pd.to_datetime(known["price_time"], utc=True).max() == pd.Timestamp(
        dates[1], tz="UTC"
    )
    assert all(
        account["last_market_session"] == pd.Timestamp(dates[1], tz="UTC")
        for account in result["accounts"].values()
    )


def test_daily_ledger_hash_is_stable_across_same_day_observation_retries():
    dates = _sessions()
    result = _advance(_signal(), _prices(), dates[0])
    account = result["accounts"]["short_term"]

    first = build_virtual_account_daily_state(
        account, observed_at=dates[0] + pd.Timedelta(hours=9)
    )
    retried = build_virtual_account_daily_state(
        account, observed_at=dates[0] + pd.Timedelta(hours=12)
    )

    assert first["state"]["session_date"] == retried["state"]["session_date"]
    assert first["state"]["input_data_version"] == retried["state"]["input_data_version"]
    assert first["state"]["input_hash"] == retried["state"]["input_hash"]


def test_daily_ledger_emits_decision_plan_skip_execution_closure_and_balance():
    dates = _sessions()
    first = _advance(_signal(), _prices(), dates[0])
    first_account = first["accounts"]["short_term"]
    decisions = pd.DataFrame(
        [
            {
                "decision_at": dates[0],
                "symbol": "A",
                "status": "eligible_signal",
                "decision": "買い候補",
            },
            {
                "decision_at": dates[0],
                "symbol": "B",
                "status": "below_score_threshold",
                "decision": "待機",
            },
        ]
    )
    first_types = {
        event["event_type"]
        for event in build_virtual_account_events(
            first_account, observed_at=dates[0], decisions=decisions
        )
    }

    second = _advance(
        pd.DataFrame(), _prices(), dates[1], previous_states=first["accounts"]
    )
    third = _advance(
        pd.DataFrame(), _prices(), dates[2], previous_states=second["accounts"]
    )
    third_types = {
        event["event_type"]
        for event in build_virtual_account_events(
            third["accounts"]["short_term"], observed_at=dates[2]
        )
    }

    assert {"decision", "planned_execution", "skip", "daily_balance"}.issubset(
        first_types
    )
    assert {"execution", "closure", "daily_balance"}.issubset(third_types)


def test_ledger_requires_both_independent_accounts_before_database_write():
    with pytest.raises(ValueError, match="short_term and mid_term"):
        persist_forward_accounts(
            None,
            {"accounts": {"short_term": {}}},
            observed_at=_sessions()[0],
        )
