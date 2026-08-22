import pandas as pd
import pytest

from app.backtest.asset_lifecycle import (
    AssetLifecyclePolicy,
    investable_universe_as_of,
    normalize_asset_lifecycle,
)
from app.backtest.ohlc import MarketImpactAssumptions, simulate_ohlc_portfolio
from app.backtest.portfolio import ExecutionAssumptions


def _coverage(start: str, end: str | None = None) -> pd.DataFrame:
    end = end or start
    return pd.DataFrame(
        [{
            "period_start": start,
            "period_end": end,
            "status": "complete",
            "source": "test-master",
            "observed_asset_count": 2,
            "input_hash": "test",
            "available_at": start,
            "checked_at": start,
        }]
    )


def _record(symbol: str, start: str, end: str | None = None, **overrides) -> dict:
    row = {
        "symbol": symbol,
        "effective_from": start,
        "effective_to": end,
        "listed_on": None,
        "delisted_on": None,
        "market": "Prime",
        "sector_17": "情報通信・サービスその他",
        "sector_33": "情報・通信業",
        "investability_status": "investable",
        "source": "test-master",
        "available_at": start,
        "fetched_at": start,
    }
    row.update(overrides)
    return row


def test_historical_universe_keeps_delisted_asset_in_earlier_snapshot():
    records = pd.DataFrame(
        [
            _record("SURVIVOR", "2026-01-05", "2026-01-06"),
            _record("DELISTED", "2026-01-05", "2026-01-05"),
            _record("SURVIVOR", "2026-01-06"),
        ]
    )
    coverage = pd.concat(
        [_coverage("2026-01-05"), _coverage("2026-01-06")], ignore_index=True
    )

    earlier = investable_universe_as_of(records, coverage, "2026-01-05")
    later = investable_universe_as_of(records, coverage, "2026-01-06")

    assert earlier["symbols"] == ["DELISTED", "SURVIVOR"]
    assert later["symbols"] == ["SURVIVOR"]


def test_strict_policy_rejects_entry_without_verified_historical_universe():
    dates = pd.date_range("2026-01-05", periods=3, tz="UTC")
    prices = pd.DataFrame(
        {
            "price_time": dates,
            "symbol": "A",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 10_000,
        }
    )
    signals = pd.DataFrame(
        [{
            "signal_date": dates[0],
            "entry_date": dates[1],
            "symbol": "A",
            "side": "long",
            "stop_loss": -0.05,
            "take_profit": 0.08,
            "maximum_holding_days": 5,
        }]
    )

    result = simulate_ohlc_portfolio(
        signals,
        prices,
        asset_lifecycle_policy=AssetLifecyclePolicy(missing_coverage_policy="reject"),
    )

    assert result["transactions"].empty
    assert result["rejected_signals"].iloc[0]["reason"] == "asset_universe_coverage_unverified"


def test_complete_snapshot_absence_closes_held_delisted_asset_at_zero_recovery():
    dates = pd.date_range("2026-01-05", periods=4, tz="UTC")
    prices = pd.DataFrame(
        {
            "price_time": dates,
            "symbol": "DELISTED",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 10_000,
        }
    )
    signals = pd.DataFrame(
        [{
            "signal_date": dates[0],
            "entry_date": dates[1],
            "symbol": "DELISTED",
            "sector": "current-sector-must-not-win",
            "side": "long",
            "stop_loss": -0.05,
            "take_profit": 0.50,
            "maximum_holding_days": 10,
        }]
    )
    records = pd.DataFrame([_record("DELISTED", "2026-01-05", "2026-01-06")])
    coverage = pd.concat(
        [_coverage("2026-01-05", "2026-01-06"), _coverage("2026-01-07", "2026-01-08")],
        ignore_index=True,
    )

    result = simulate_ohlc_portfolio(
        signals,
        prices,
        initial_cash=1_000,
        assumptions=ExecutionAssumptions(
            fee_rate=0, spread_rate=0, lot_size=1, maximum_position_rate=1
        ),
        market_impact=MarketImpactAssumptions(base_slippage_rate=0, impact_rate=0),
        asset_lifecycle=records,
        asset_universe_coverage=coverage,
        asset_lifecycle_policy=AssetLifecyclePolicy(missing_coverage_policy="reject"),
    )

    closed = result["transactions"].query("action == '上場廃止評価'").iloc[0]
    assert closed["execution_price"] == 0
    assert closed["trade_return"] == -1
    assert closed["reason"] == "absent_from_complete_asset_universe"
    assert result["positions"].empty
    assert result["metrics"]["closed_trades"] == 1
    assert result["asset_lifecycle_policy"].version == "asset-lifecycle-conservative-v1"


def test_lifecycle_validation_rejects_invalid_intervals_and_status():
    with pytest.raises(ValueError, match="effective_from"):
        normalize_asset_lifecycle(
            pd.DataFrame([_record("A", "2026-01-06", "2026-01-05")])
        )
    with pytest.raises(ValueError, match="investability_status"):
        normalize_asset_lifecycle(
            pd.DataFrame([_record("A", "2026-01-05", investability_status="maybe")])
        )
