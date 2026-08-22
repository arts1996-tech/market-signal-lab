import pandas as pd
import pytest

from app.analysis.market_calendar import exchange_calendar
from app.backtest.corporate_actions import (
    CorporateActionPolicy,
    normalize_corporate_actions,
)
from app.backtest.ohlc import MarketImpactAssumptions, simulate_ohlc_portfolio
from app.backtest.portfolio import ExecutionAssumptions


def _sessions(count: int = 7) -> pd.DatetimeIndex:
    return exchange_calendar("XTKS").sessions_in_range(
        "2026-01-01", "2026-02-28"
    )[:count]


def _prices(*, adjusted: bool = False, split_at: int | None = None) -> pd.DataFrame:
    sessions = _sessions()
    raw = [100.0] * len(sessions)
    if split_at is not None:
        raw[split_at:] = [50.0] * (len(sessions) - split_at)
    frame = pd.DataFrame(
        {
            "price_time": sessions,
            "symbol": "A",
            "open": [100.0] * len(sessions) if adjusted else raw,
            "high": [101.0] * len(sessions)
            if adjusted
            else [value * 1.01 for value in raw],
            "low": [99.0] * len(sessions)
            if adjusted
            else [value * 0.99 for value in raw],
            "close": [100.0] * len(sessions) if adjusted else raw,
            "volume": [10_000.0] * len(sessions),
            "source": "demo",
        }
    )
    if adjusted:
        frame["price_basis"] = "raw_ohlcv_with_adjusted"
        frame["raw_open"] = raw
        frame["raw_high"] = [value * 1.01 for value in raw]
        frame["raw_low"] = [value * 0.99 for value in raw]
        frame["raw_close"] = raw
        frame["raw_volume"] = 10_000.0
    return frame


def _signal(**overrides) -> pd.DataFrame:
    sessions = _sessions()
    row = {
        "signal_date": sessions[0],
        "entry_date": sessions[1],
        "symbol": "A",
        "name": "A",
        "sector": "test",
        "score": 80,
        "side": "long",
        "stop_loss": -0.20,
        "take_profit": 0.50,
        "maximum_holding_days": 20,
        "reasons": ["synthetic corporate-action test"],
    }
    row.update(overrides)
    return pd.DataFrame([row])


def _coverage() -> pd.DataFrame:
    sessions = _sessions()
    return pd.DataFrame(
        [
            {
                "symbol": "A",
                "period_start": sessions[0],
                "period_end": sessions[-1],
                "status": "complete",
                "source": "reviewed_test_fixture",
                "checked_at": sessions[-1],
            }
        ]
    )


def _execution() -> tuple[ExecutionAssumptions, MarketImpactAssumptions]:
    return (
        ExecutionAssumptions(
            fee_rate=0,
            spread_rate=0,
            tax_rate=0,
            lot_size=1,
            maximum_position_rate=1,
        ),
        MarketImpactAssumptions(base_slippage_rate=0, impact_rate=0),
    )


def _simulate(actions, *, prices=None, coverage=None, policy=None):
    assumptions, market_impact = _execution()
    return simulate_ohlc_portfolio(
        _signal(),
        _prices() if prices is None else prices,
        initial_cash=100_000,
        assumptions=assumptions,
        market_impact=market_impact,
        corporate_actions=pd.DataFrame(actions),
        corporate_action_coverage=_coverage() if coverage is None else coverage,
        corporate_action_policy=policy,
    )


def test_cash_dividend_requires_ex_record_and_payable_dates_in_order():
    sessions = _sessions()
    with pytest.raises(ValueError, match="ex <= record <= payable"):
        normalize_corporate_actions(
            pd.DataFrame(
                [
                    {
                        "action_id": "bad-dividend",
                        "symbol": "A",
                        "action_type": "cash_dividend",
                        "ex_date": sessions[3],
                        "record_date": sessions[2],
                        "payable_date": sessions[4],
                        "cash_per_share": 2,
                        "currency": "JPY",
                    }
                ]
            )
        )


def test_announcement_timestamp_is_preserved_for_point_in_time_gating():
    sessions = _sessions()
    announced = sessions[0].tz_localize("UTC") + pd.Timedelta(hours=6)
    normalized = normalize_corporate_actions(
        pd.DataFrame(
            [
                {
                    "action_id": "split-intraday-announcement",
                    "symbol": "A",
                    "action_type": "stock_split",
                    "announced_at": announced,
                    "effective_date": sessions[3],
                    "ratio": 2,
                    "source": "test",
                }
            ]
        )
    )

    assert normalized.iloc[0]["announced_at"] == announced


def test_missing_coverage_warns_or_rejects_according_to_policy():
    warning = _simulate([], coverage=pd.DataFrame())
    assert warning["evaluation_status"] == "warning"
    assert "corporate_action_coverage_unverified" in warning["quality_warnings"]

    rejected = _simulate(
        [],
        coverage=pd.DataFrame(),
        policy=CorporateActionPolicy(missing_coverage_policy="reject"),
    )
    assert rejected["transactions"].empty
    assert rejected["rejected_signals"].iloc[0]["reason"] == (
        "corporate_action_coverage_unverified"
    )


def test_split_uses_raw_prices_and_adjusts_quantity_without_double_counting():
    sessions = _sessions()
    result = _simulate(
        [
            {
                "action_id": "split-1",
                "symbol": "A",
                "action_type": "stock_split",
                "announced_at": sessions[0],
                "effective_date": sessions[3],
                "ratio": 2,
                "source": "test",
            }
        ],
        prices=_prices(adjusted=True, split_at=3),
    )

    entry_quantity = int(
        result["transactions"].query("action == '仮想エントリー'").iloc[0]["quantity"]
    )
    split = result["transactions"].query("action == '株式分割'").iloc[0]
    assert split["previous_quantity"] == entry_quantity
    assert split["quantity"] == entry_quantity * 2
    assert result["positions"].iloc[0]["quantity"] == entry_quantity * 2
    assert result["positions"].iloc[0]["market_value"] == pytest.approx(
        entry_quantity * 100
    )
    assert result["evaluation_status"] == "complete"


def test_cash_dividend_is_entitled_on_ex_date_and_credited_on_payable_date():
    sessions = _sessions()
    result = _simulate(
        [
            {
                "action_id": "dividend-1",
                "symbol": "A",
                "action_type": "cash_dividend",
                "announced_at": sessions[0],
                "ex_date": sessions[2],
                "record_date": sessions[3],
                "payable_date": sessions[5],
                "cash_per_share": 2,
                "currency": "JPY",
                "source": "test",
            }
        ]
    )

    entry_quantity = int(
        result["transactions"].query("action == '仮想エントリー'").iloc[0]["quantity"]
    )
    payment = result["transactions"].query("action == '現金配当'").iloc[0]
    assert payment["date"] == sessions[5].tz_localize("UTC")
    assert payment["amount"] == pytest.approx(entry_quantity * 2)
    assert result["dividend_income"] == pytest.approx(entry_quantity * 2)
    assert result["pending_dividends"].empty


def test_cash_dividend_uses_raw_ex_dividend_price_without_double_counting():
    sessions = _sessions()
    prices = _prices(adjusted=True)
    after_ex = prices["price_time"] >= sessions[2]
    prices.loc[after_ex, ["raw_open", "raw_close"]] = 98.0
    prices.loc[after_ex, "raw_high"] = 98.98
    prices.loc[after_ex, "raw_low"] = 97.02
    result = _simulate(
        [
            {
                "action_id": "dividend-adjusted-price",
                "symbol": "A",
                "action_type": "cash_dividend",
                "announced_at": sessions[0],
                "ex_date": sessions[2],
                "record_date": sessions[3],
                "payable_date": sessions[5],
                "cash_per_share": 2,
                "currency": "JPY",
                "source": "test",
            }
        ],
        prices=prices,
    )

    assert result["equity"] == pytest.approx(100_000)
    assert result["dividend_income"] > 0


def test_known_merger_inside_horizon_rejects_new_entry():
    sessions = _sessions()
    result = _simulate(
        [
            {
                "action_id": "merger-1",
                "symbol": "A",
                "action_type": "merger",
                "announced_at": sessions[0],
                "effective_date": sessions[3],
                "source": "test",
            }
        ]
    )

    assert result["transactions"].empty
    assert result["rejected_signals"].iloc[0]["reason"] == (
        "known_unsupported_corporate_action"
    )
    assert "unsupported_corporate_action_present" in result["quality_warnings"]


def test_cancelled_merger_does_not_reject_entry_or_raise_unsupported_warning():
    sessions = _sessions()
    result = _simulate(
        [
            {
                "action_id": "cancelled-merger",
                "symbol": "A",
                "action_type": "merger",
                "announced_at": sessions[0],
                "effective_date": sessions[3],
                "status": "cancelled",
                "source": "test",
            }
        ]
    )

    assert not result["transactions"].query("action == '仮想エントリー'").empty
    assert "unsupported_corporate_action_present" not in result["quality_warnings"]


def test_pending_split_defers_open_position_evaluation():
    sessions = _sessions()
    result = _simulate(
        [
            {
                "action_id": "pending-split",
                "symbol": "A",
                "action_type": "stock_split",
                "announced_at": sessions[0],
                "effective_date": sessions[3],
                "ratio": 2,
                "status": "pending",
                "source": "test",
            }
        ]
    )

    assert result["evaluation_status"] == "incomplete"
    assert "unconfirmed_corporate_action_present" in result["quality_warnings"]
    assert result["corporate_action_events"].iloc[0]["reason"] == (
        "corporate_action_not_confirmed"
    )


def test_fractional_reverse_split_defers_evaluation_instead_of_inventing_cash():
    sessions = _sessions()
    result = _simulate(
        [
            {
                "action_id": "reverse-1",
                "symbol": "A",
                "action_type": "reverse_split",
                "announced_at": sessions[0],
                "effective_date": sessions[3],
                "ratio": 0.33,
                "source": "test",
            }
        ],
        prices=_prices(adjusted=True, split_at=None),
    )

    entry_quantity = int(
        result["transactions"].query("action == '仮想エントリー'").iloc[0]["quantity"]
    )
    assert not float(entry_quantity * 0.33).is_integer()
    assert result["evaluation_status"] == "incomplete"
    event = result["corporate_action_events"].iloc[0]
    assert event["reason"] == "fractional_share_cash_in_lieu_unmodeled"
