from pathlib import Path

import pandas as pd

from app.analysis.market_calendar import exchange_calendar
from app.services import backtest_service


def _prices(periods: int = 100) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = exchange_calendar("XTKS").sessions_in_range("2025-01-01", "2026-12-31")[:periods]
    rows = []
    for symbol, offset in (("10010", 0.0), ("10020", 50.0)):
        for index, session in enumerate(dates):
            close = 1_000 + offset + index * 2
            rows.append(
                {
                    "symbol": symbol,
                    "name": symbol,
                    "price_time": session,
                    "open": close - 1,
                    "high": close + 5,
                    "low": close - 5,
                    "close": close,
                    "volume": 1_000_000,
                    "source": "jquants",
                    "price_basis": "raw_ohlcv_with_adjusted",
                }
            )
    index_rows = [
        {
            "symbol": "NIKKEI225",
            "price_time": session,
            "close": 30_000 + index * 10,
        }
        for index, session in enumerate(dates)
    ]
    return pd.DataFrame(rows), pd.DataFrame(index_rows)


def test_real_backtest_reports_explicit_history_shortfall(tmp_path):
    prices, index_prices = _prices(30)
    rule = backtest_service.RealBacktestRule(
        account_name="short_term",
        strategy_version="test-rule-v1",
        score_threshold=70,
        stop_loss=-0.05,
        take_profit=0.08,
        maximum_holding_days=5,
        minimum_train_sessions=40,
        test_sessions=20,
    )

    result = backtest_service.evaluate_real_account_walk_forward(
        prices,
        index_prices,
        rule=rule,
        validation_registry_path=tmp_path / "windows.json",
    )

    assert result["status"] == "insufficient_data"
    assert result["reasons"] == ["insufficient_contiguous_price_history"]
    assert result["maximum_contiguous_sessions"] == 30
    assert result["input_data_version"]
    assert result["rule_hash"]
    assert result["execution_assumptions"]["fee_rate"] == 0.001
    assert not (tmp_path / "windows.json").exists()


def test_real_backtest_claims_unseen_windows_before_evaluation(monkeypatch, tmp_path):
    prices, index_prices = _prices(100)
    rule = backtest_service.RealBacktestRule(
        account_name="short_term",
        strategy_version="test-rule-v1",
        score_threshold=70,
        stop_loss=-0.05,
        take_profit=0.08,
        maximum_holding_days=5,
        minimum_train_sessions=40,
        test_sessions=20,
    )

    def point_in_time_signals(_index, price_frame, **_kwargs):
        dates = pd.DatetimeIndex(sorted(price_frame["price_time"].unique()))
        return pd.DataFrame(
            [
                {
                    "signal_date": dates[location],
                    "entry_date": dates[location + 1],
                    "symbol": "10010",
                    "name": "10010",
                    "score": 80,
                    "minimum_score": 70,
                    "side": "long",
                    "maximum_holding_days": 5,
                    "reasons": ["point-in-time test signal"],
                }
                for location in range(45, len(dates) - 6, 10)
            ]
        )

    monkeypatch.setattr(
        backtest_service,
        "build_point_in_time_historical_signals",
        point_in_time_signals,
    )
    registry = tmp_path / "windows.json"

    result = backtest_service.evaluate_real_account_walk_forward(
        prices,
        index_prices,
        rule=rule,
        validation_registry_path=registry,
    )

    assert result["status"] == "success"
    assert result["validation_window_count"] == 3
    assert result["validation_test_signals"] > 0
    assert result["validation_closed_trades"] > 0
    assert result["benchmark"] == "NIKKEI225"
    assert result["execution_assumptions"]["tax_rate"] == 0.0
    assert all(window["validation_claim_id"] for window in result["windows"])
    assert all(window["rule_frozen_before_validation"] for window in result["windows"])
    assert all(window["training_input_hash"] for window in result["windows"])
    assert all(window["validation_input_hash"] for window in result["windows"])
    assert result["forward_period"]["status"] == "awaiting_observations"
    assert result["forward_period"]["validation_data_reused"] is False
    assert result["forward_period"]["activation_claim_id"]
    assert registry.exists()


def test_real_backtest_keeps_new_observations_out_of_frozen_validation(
    monkeypatch, tmp_path
):
    prices, index_prices = _prices(100)
    rule = backtest_service.RealBacktestRule(
        account_name="short_term",
        strategy_version="test-rule-v1",
        score_threshold=70,
        stop_loss=-0.05,
        take_profit=0.08,
        maximum_holding_days=5,
        minimum_train_sessions=40,
        test_sessions=20,
    )

    def point_in_time_signals(_index, price_frame, **_kwargs):
        dates = pd.DatetimeIndex(sorted(price_frame["price_time"].unique()))
        return pd.DataFrame(
            [
                {
                    "signal_date": dates[location],
                    "entry_date": dates[location + 1],
                    "symbol": "10010",
                    "name": "10010",
                    "score": 80,
                    "minimum_score": 70,
                    "side": "long",
                    "maximum_holding_days": 5,
                    "reasons": ["frozen signal"],
                }
                for location in range(45, len(dates) - 1, 10)
            ]
        )

    monkeypatch.setattr(
        backtest_service,
        "build_point_in_time_historical_signals",
        point_in_time_signals,
    )
    registry = tmp_path / "windows.json"
    first = backtest_service.evaluate_real_account_walk_forward(
        prices,
        index_prices,
        rule=rule,
        validation_registry_path=registry,
    )
    extended_prices, extended_index = _prices(120)
    repeated = backtest_service.evaluate_real_account_walk_forward(
        extended_prices,
        extended_index,
        rule=rule,
        validation_registry_path=registry,
    )

    assert repeated["validation_window_count"] == first["validation_window_count"]
    assert repeated["forward_period"]["forward_start"] == first["forward_period"][
        "forward_start"
    ]
    assert repeated["forward_period"]["observed_sessions"] == 20
    assert repeated["forward_period"]["status"] == "reserved_unscored"


def test_real_backtest_does_not_relabel_already_observed_partial_window_as_forward(
    monkeypatch, tmp_path
):
    prices, index_prices = _prices(110)
    rule = backtest_service.RealBacktestRule(
        account_name="short_term",
        strategy_version="test-rule-v1",
        score_threshold=70,
        stop_loss=-0.05,
        take_profit=0.08,
        maximum_holding_days=5,
        minimum_train_sessions=40,
        test_sessions=20,
    )

    def point_in_time_signals(_index, price_frame, **_kwargs):
        dates = pd.DatetimeIndex(sorted(price_frame["price_time"].unique()))
        return pd.DataFrame(
            [
                {
                    "signal_date": dates[50],
                    "entry_date": dates[51],
                    "symbol": "10010",
                    "side": "long",
                    "maximum_holding_days": 5,
                    "reasons": ["frozen signal"],
                }
            ]
        )

    monkeypatch.setattr(
        backtest_service,
        "build_point_in_time_historical_signals",
        point_in_time_signals,
    )

    result = backtest_service.evaluate_real_account_walk_forward(
        prices,
        index_prices,
        rule=rule,
        validation_registry_path=tmp_path / "windows.json",
    )

    assert result["validation_window_count"] == 3
    assert result["forward_period"]["embargoed_sessions_after_validation"] == 10
    assert result["forward_period"]["observed_sessions"] == 0
    assert pd.Timestamp(result["forward_period"]["forward_start"]).tz_localize(
        None
    ) > pd.Timestamp(prices["price_time"].max())


def test_normal_backtest_job_no_longer_records_placeholder_success():
    source = Path("jobs/run_backtest.py").read_text(encoding="utf-8")

    assert '"placeholder"' not in source
    assert "run_real_walk_forward_backtest" in source
    assert 'details["status"]' in source
