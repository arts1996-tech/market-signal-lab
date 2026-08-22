import json
from decimal import Decimal

import pandas as pd
import pytest

from app.analysis.market_calendar import exchange_calendar
from app.backtest.audit import build_run_manifest, frame_hash
from app.backtest.ohlc import (
    MarketImpactAssumptions,
    PortfolioRiskRules,
    simulate_ohlc_portfolio,
)
from app.backtest.portfolio import ExecutionAssumptions
from app.backtest.shadow import write_forward_shadow_snapshot
from app.backtest.validation import evaluate_frozen_strategy_walk_forward, walk_forward_windows
from app.backtest.validation_registry import (
    ValidationWindowConflict,
    claim_forward_period,
    claim_validation_window,
)


def _sessions(count: int = 12) -> pd.DatetimeIndex:
    return exchange_calendar("XTKS").sessions_in_range("2026-01-01", "2026-03-31")[:count]


def _prices(
    *,
    symbol: str = "A",
    opens: list[float] | None = None,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    closes: list[float] | None = None,
    volumes: list[float] | None = None,
) -> pd.DataFrame:
    closes = closes or [100.0] * 12
    count = len(closes)
    dates = _sessions(count)
    opens = opens or list(closes)
    highs = highs or [value * 1.01 for value in closes]
    lows = lows or [value * 0.99 for value in closes]
    volumes = volumes or [10_000.0] * count
    return pd.DataFrame(
        {
            "price_time": dates,
            "symbol": symbol,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
            "source": "demo",
        }
    )


def _signal(symbol: str = "A", entry_location: int = 2, **overrides) -> pd.DataFrame:
    dates = _sessions()
    values = {
        "signal_date": dates[entry_location - 1],
        "entry_date": dates[entry_location],
        "symbol": symbol,
        "name": symbol,
        "sector": "technology",
        "score": 80,
        "side": "long",
        "minimum_score": 70,
        "stop_loss": -0.05,
        "take_profit": 0.08,
        "maximum_holding_days": 5,
        "reasons": ["test signal"],
    }
    values.update(overrides)
    return pd.DataFrame([values])


def test_ohlc_engine_uses_conservative_stop_when_both_levels_hit():
    prices = _prices()
    entry_date = _sessions()[2]
    prices.loc[prices["price_time"] == entry_date, ["high", "low"]] = [120.0, 90.0]

    result = simulate_ohlc_portfolio(
        _signal(),
        prices,
        assumptions=ExecutionAssumptions(fee_rate=0, spread_rate=0, lot_size=1),
        market_impact=MarketImpactAssumptions(base_slippage_rate=0, impact_rate=0),
    )

    exit_row = result["transactions"].query("action == '損切り'").iloc[0]
    assert exit_row["execution_price"] == pytest.approx(95.0)
    assert exit_row["reason"] == "損切り条件成立"
    assert result["metrics"]["win_rate_ci95"] is not None
    assert result["metrics"]["average_trade_return_ci95"] is None


def test_ohlc_engine_executes_gap_stop_at_open_not_stop_price():
    prices = _prices()
    dates = _sessions()
    prices.loc[prices["price_time"] == dates[3], ["open", "high", "low", "close"]] = [90, 92, 88, 91]

    result = simulate_ohlc_portfolio(
        _signal(maximum_holding_days=6),
        prices,
        assumptions=ExecutionAssumptions(fee_rate=0, spread_rate=0, lot_size=1),
        market_impact=MarketImpactAssumptions(base_slippage_rate=0, impact_rate=0),
    )

    exit_row = result["transactions"].query("action == '損切り'").iloc[0]
    assert exit_row["execution_price"] == 90
    assert "窓開け" in exit_row["reason"]


def test_liquidity_sizing_uses_previous_volume_and_partial_fill():
    prices = _prices(volumes=[1_000, 1_000, 1_000_000] + [1_000] * 9)
    impact = MarketImpactAssumptions(
        maximum_volume_participation=0.10,
        allow_partial_fill=True,
        base_slippage_rate=0,
        impact_rate=0,
    )

    result = simulate_ohlc_portfolio(
        _signal(),
        prices,
        initial_cash=1_000_000,
        assumptions=ExecutionAssumptions(
            fee_rate=0,
            spread_rate=0,
            lot_size=10,
            maximum_position_rate=1,
        ),
        market_impact=impact,
    )

    entry = result["transactions"].query("action == '仮想エントリー'").iloc[0]
    assert entry["quantity"] == 100
    assert entry["participation_rate"] == pytest.approx(0.10)


@pytest.mark.parametrize(
    ("previous_volume", "expected_profile", "expected_spread"),
    [
        (10_000_000, "turnover_cost_v1:high", 0.0005),
        (3_000_000, "turnover_cost_v1:medium", 0.0010),
        (600_000, "turnover_cost_v1:low", 0.0020),
    ],
)
def test_turnover_cost_model_selects_conservative_liquidity_tier(
    previous_volume, expected_profile, expected_spread
):
    prices = _prices(volumes=[previous_volume] * 12)

    result = simulate_ohlc_portfolio(
        _signal(),
        prices,
        assumptions=ExecutionAssumptions(fee_rate=0, spread_rate=0, lot_size=1),
        market_impact=MarketImpactAssumptions(
            use_turnover_cost_model=True,
            minimum_previous_turnover=50_000_000,
            impact_rate=0,
        ),
    )

    entry = result["transactions"].query("action == '仮想エントリー'").iloc[0]
    assert entry["execution_cost_profile"] == expected_profile
    assert entry["spread_rate"] == pytest.approx(expected_spread)
    assert entry["previous_turnover"] == pytest.approx(previous_volume * 100)


def test_turnover_cost_model_rejects_symbol_below_minimum_turnover():
    result = simulate_ohlc_portfolio(
        _signal(),
        _prices(volumes=[499_999] * 12),
        market_impact=MarketImpactAssumptions(
            use_turnover_cost_model=True,
            minimum_previous_turnover=50_000_000,
        ),
    )

    assert result["transactions"].empty
    assert set(result["rejected_signals"]["reason"]) == {"minimum_turnover_not_met"}


def test_signal_specific_execution_costs_override_turnover_tier():
    result = simulate_ohlc_portfolio(
        _signal(spread_rate=0.004, base_slippage_rate=0.002, impact_rate=0),
        _prices(volumes=[600_000] * 12),
        assumptions=ExecutionAssumptions(fee_rate=0, spread_rate=0, lot_size=1),
        market_impact=MarketImpactAssumptions(use_turnover_cost_model=True),
    )

    entry = result["transactions"].query("action == '仮想エントリー'").iloc[0]
    assert entry["execution_cost_profile"] == "signal_specific_v1"
    assert entry["spread_rate"] == pytest.approx(0.004)
    assert entry["slippage_rate"] == pytest.approx(0.002)
    assert entry["execution_price"] == pytest.approx(100.4)


def test_invalid_signal_specific_execution_cost_is_rejected():
    result = simulate_ohlc_portfolio(
        _signal(spread_rate=0.06),
        _prices(),
    )

    assert result["transactions"].empty
    assert set(result["rejected_signals"]["reason"]) == {
        "execution_cost_profile_unavailable"
    }


def test_special_quote_rejects_entry():
    prices = _prices()
    prices["special_quote"] = False
    prices.loc[prices["price_time"] == _sessions()[2], "special_quote"] = True

    result = simulate_ohlc_portfolio(_signal(), prices)

    assert result["transactions"].empty
    assert set(result["rejected_signals"]["reason"]) == {"special_quote_no_fill"}


def test_special_quote_defers_exit_until_next_tradable_session():
    prices = _prices()
    dates = _sessions()
    prices["special_quote"] = False
    prices.loc[prices["price_time"] == dates[3], ["open", "high", "low", "close"]] = [
        90,
        91,
        88,
        89,
    ]
    prices.loc[prices["price_time"] == dates[3], "special_quote"] = True
    prices.loc[prices["price_time"] == dates[4], ["open", "high", "low", "close"]] = [
        91,
        92,
        90,
        91,
    ]

    result = simulate_ohlc_portfolio(
        _signal(maximum_holding_days=8),
        prices,
        assumptions=ExecutionAssumptions(fee_rate=0, spread_rate=0, lot_size=1),
        market_impact=MarketImpactAssumptions(base_slippage_rate=0, impact_rate=0),
    )

    exit_row = result["transactions"].query("action == '損切り'").iloc[0]
    assert exit_row["date"] == pd.Timestamp(dates[4], tz="UTC")
    assert exit_row["execution_price"] == 91


def test_missing_previous_volume_rejects_when_volume_is_required():
    prices = _prices()
    prices.loc[prices["price_time"] == _sessions()[1], "volume"] = float("nan")

    result = simulate_ohlc_portfolio(
        _signal(), prices, market_impact=MarketImpactAssumptions(require_volume=True)
    )

    assert result["transactions"].empty
    assert set(result["rejected_signals"]["reason"]) == {"missing_previous_volume"}


def test_sector_concentration_caps_second_position():
    prices = pd.concat([_prices(symbol="A"), _prices(symbol="B")], ignore_index=True)
    signals = pd.concat([_signal("A"), _signal("B")], ignore_index=True)
    result = simulate_ohlc_portfolio(
        signals,
        prices,
        initial_cash=1_000_000,
        assumptions=ExecutionAssumptions(
            fee_rate=0,
            spread_rate=0,
            lot_size=100,
            maximum_positions=2,
            maximum_position_rate=0.4,
        ),
        market_impact=MarketImpactAssumptions(base_slippage_rate=0, impact_rate=0),
        risk_rules=PortfolioRiskRules(maximum_sector_rate=0.5),
    )

    entries = result["transactions"].query("action == '仮想エントリー'")
    assert entries["amount"].sum() <= 500_000


def test_position_correlation_limit_uses_only_information_available_at_signal_time():
    dates = _sessions()
    closes_a = [100, 101, 103, 102, 105, 107, 106, 109, 111, 114, 116, 118]
    closes_b = [200, 202, 206, 204, 210, 214, 212, 218, 222, 190, 180, 170]
    prices = pd.concat(
        [
            _prices(symbol="A", closes=closes_a),
            _prices(symbol="B", closes=closes_b),
        ],
        ignore_index=True,
    )
    signals = pd.concat(
        [
            _signal(
                "A",
                entry_location=2,
                maximum_holding_days=20,
                stop_loss=-0.50,
                take_profit=1.0,
            ),
            _signal(
                "B",
                entry_location=9,
                sector="healthcare",
                maximum_holding_days=20,
                stop_loss=-0.50,
                take_profit=1.0,
            ),
        ],
        ignore_index=True,
    )
    result = simulate_ohlc_portfolio(
        signals,
        prices,
        assumptions=ExecutionAssumptions(
            fee_rate=0,
            spread_rate=0,
            lot_size=1,
            maximum_positions=2,
            maximum_position_rate=0.20,
        ),
        market_impact=MarketImpactAssumptions(base_slippage_rate=0, impact_rate=0),
        risk_rules=PortfolioRiskRules(
            maximum_sector_rate=1,
            maximum_risk_per_trade_rate=0.10,
            maximum_total_open_risk_rate=0.20,
            maximum_position_correlation=0.80,
            correlation_lookback_sessions=8,
            minimum_correlation_observations=5,
        ),
    )

    entries = result["transactions"].query("action == '仮想エントリー'")
    assert list(entries["symbol"]) == ["A"]
    rejected = result["rejected_signals"].set_index("symbol")
    assert rejected.loc["B", "reason"] == "position_correlation_limit"
    assert dates[9] > signals.loc[1, "signal_date"]


def test_manifest_is_deterministic_except_created_at():
    signals = _signal()
    prices = _prices()
    assumptions = ExecutionAssumptions()
    risks = PortfolioRiskRules()

    first = build_run_manifest(
        signals, prices, account_name="short", assumptions=assumptions, risk_rules=risks
    )
    second = build_run_manifest(
        signals, prices, account_name="short", assumptions=assumptions, risk_rules=risks
    )

    assert first["run_id"] == second["run_id"]
    assert first["price_hash"] == second["price_hash"]

    reversed_signals = signals.iloc[::-1].reset_index(drop=True)
    reordered = build_run_manifest(
        reversed_signals,
        prices,
        account_name="short",
        assumptions=assumptions,
        risk_rules=risks,
    )
    assert first["signal_hash"] == reordered["signal_hash"]


def test_frame_hash_normalizes_postgresql_decimal_values():
    frame = pd.DataFrame([{"symbol": "A", "close": Decimal("123.4500")}])

    assert frame_hash(frame) == frame_hash(
        pd.DataFrame([{"symbol": "A", "close": 123.45}])
    )


def test_manifest_hash_changes_when_execution_availability_flag_changes():
    prices = _prices()
    prices["special_quote"] = False
    assumptions = ExecutionAssumptions()
    risks = PortfolioRiskRules()
    available = build_run_manifest(
        _signal(), prices, account_name="short", assumptions=assumptions, risk_rules=risks
    )
    prices.loc[prices.index[-1], "special_quote"] = True
    restricted = build_run_manifest(
        _signal(), prices, account_name="short", assumptions=assumptions, risk_rules=risks
    )

    assert available["price_hash"] != restricted["price_hash"]
    assert available["run_id"] != restricted["run_id"]


def test_ohlc_engine_reports_multiple_benchmark_comparisons():
    dates = _sessions()
    primary = pd.Series([100 + index for index in range(len(dates))], index=dates)
    simple_hold = pd.Series([100 + index * 2 for index in range(len(dates))], index=dates)

    result = simulate_ohlc_portfolio(
        _signal(),
        _prices(),
        benchmark=primary,
        benchmarks={"simple_hold": simple_hold},
    )

    comparisons = result["benchmark_comparisons"].set_index("benchmark")
    assert set(comparisons.index) == {"primary", "simple_hold"}
    assert result["metrics"]["benchmark_return"] == pytest.approx(
        comparisons.loc["primary", "benchmark_return"]
    )
    assert comparisons.loc["simple_hold", "benchmark_return"] > comparisons.loc[
        "primary", "benchmark_return"
    ]


def test_limit_down_defers_stop_exit_until_a_tradable_session():
    prices = _prices()
    dates = _sessions()
    prices["limit_down"] = False
    prices.loc[prices["price_time"] == dates[3], ["open", "high", "low", "close"]] = [
        90,
        91,
        88,
        89,
    ]
    prices.loc[prices["price_time"] == dates[3], "limit_down"] = True
    prices.loc[prices["price_time"] == dates[4], ["open", "high", "low", "close"]] = [
        91,
        92,
        90,
        91,
    ]

    result = simulate_ohlc_portfolio(
        _signal(maximum_holding_days=8),
        prices,
        assumptions=ExecutionAssumptions(fee_rate=0, spread_rate=0, lot_size=1),
        market_impact=MarketImpactAssumptions(base_slippage_rate=0, impact_rate=0),
    )

    exit_row = result["transactions"].query("action == '損切り'").iloc[0]
    assert exit_row["date"] == pd.Timestamp(dates[4], tz="UTC")
    assert exit_row["execution_price"] == 91
    timeline = result["decision_cards"]
    assert list(timeline["status"]) == ["entered", "exit_deferred", "closed"]
    assert timeline["card_id"].nunique() == 1
    assert timeline["event_id"].nunique() == 3
    assert pd.to_datetime(timeline["event_at"], utc=True).is_monotonic_increasing


def test_liquidity_limit_rejects_when_partial_fill_is_disabled():
    prices = _prices(volumes=[100, 100] + [10_000] * 10)
    result = simulate_ohlc_portfolio(
        _signal(),
        prices,
        assumptions=ExecutionAssumptions(
            fee_rate=0,
            spread_rate=0,
            lot_size=10,
            maximum_position_rate=1,
        ),
        market_impact=MarketImpactAssumptions(
            maximum_volume_participation=0.10,
            allow_partial_fill=False,
            base_slippage_rate=0,
            impact_rate=0,
        ),
    )

    assert result["transactions"].empty
    assert set(result["rejected_signals"]["reason"]) == {"liquidity_limit"}


def test_portfolio_drawdown_halts_later_entries():
    prices_a = _prices(symbol="A")
    prices_b = _prices(symbol="B")
    dates = _sessions()
    prices_a.loc[
        prices_a["price_time"] == dates[2], ["open", "high", "low", "close"]
    ] = [100, 101, 40, 45]
    signals = pd.concat(
        [
            _signal("A", entry_location=2, stop_loss=-0.50),
            _signal("B", entry_location=3),
        ],
        ignore_index=True,
    )

    result = simulate_ohlc_portfolio(
        signals,
        pd.concat([prices_a, prices_b], ignore_index=True),
        initial_cash=1_000_000,
        assumptions=ExecutionAssumptions(
            fee_rate=0,
            spread_rate=0,
            lot_size=1,
            maximum_position_rate=0.50,
        ),
        market_impact=MarketImpactAssumptions(
            maximum_volume_participation=1,
            base_slippage_rate=0,
            impact_rate=0,
        ),
        risk_rules=PortfolioRiskRules(
            maximum_sector_rate=1,
            maximum_drawdown=0.20,
            maximum_risk_per_trade_rate=0.50,
            maximum_total_open_risk_rate=0.50,
        ),
    )

    assert result["risk_halted"] is True
    assert "portfolio_drawdown_halt" in set(result["rejected_signals"]["reason"])


def test_loss_streak_cooldown_blocks_later_entry():
    prices_a = _prices(symbol="A")
    prices_b = _prices(symbol="B")
    dates = _sessions()
    prices_a.loc[
        prices_a["price_time"] == dates[2], ["open", "high", "low", "close"]
    ] = [100, 101, 90, 95]
    signals = pd.concat([_signal("A", entry_location=2), _signal("B", entry_location=3)])

    result = simulate_ohlc_portfolio(
        signals,
        pd.concat([prices_a, prices_b], ignore_index=True),
        assumptions=ExecutionAssumptions(fee_rate=0, spread_rate=0, lot_size=1),
        market_impact=MarketImpactAssumptions(base_slippage_rate=0, impact_rate=0),
        risk_rules=PortfolioRiskRules(
            maximum_sector_rate=1,
            maximum_drawdown=0.90,
            loss_streak_threshold=1,
            cooldown_sessions=2,
        ),
    )

    assert "loss_streak_cooldown" in set(result["rejected_signals"]["reason"])


def test_position_size_is_capped_by_planned_stop_loss_risk():
    result = simulate_ohlc_portfolio(
        _signal(stop_loss=-0.05),
        _prices(),
        initial_cash=1_000_000,
        assumptions=ExecutionAssumptions(
            fee_rate=0,
            spread_rate=0,
            lot_size=1,
            maximum_position_rate=1,
        ),
        market_impact=MarketImpactAssumptions(
            maximum_volume_participation=1,
            base_slippage_rate=0,
            impact_rate=0,
        ),
        risk_rules=PortfolioRiskRules(
            maximum_sector_rate=1,
            maximum_risk_per_trade_rate=0.01,
            maximum_total_open_risk_rate=0.05,
        ),
    )

    entry = result["transactions"].query("action == '仮想エントリー'").iloc[0]
    card = result["decision_cards"].iloc[0]
    assert entry["quantity"] == 2_000
    assert card["reference_quantity"] == 2_000
    assert card["planned_risk"] == pytest.approx(10_000)


def test_total_open_risk_caps_second_position():
    signals = pd.concat([_signal("A"), _signal("B")], ignore_index=True)
    prices = pd.concat([_prices(symbol="A"), _prices(symbol="B")], ignore_index=True)
    result = simulate_ohlc_portfolio(
        signals,
        prices,
        initial_cash=1_000_000,
        assumptions=ExecutionAssumptions(
            fee_rate=0,
            spread_rate=0,
            lot_size=1,
            maximum_positions=2,
            maximum_position_rate=1,
        ),
        market_impact=MarketImpactAssumptions(
            maximum_volume_participation=1,
            base_slippage_rate=0,
            impact_rate=0,
        ),
        risk_rules=PortfolioRiskRules(
            maximum_sector_rate=1,
            maximum_risk_per_trade_rate=0.03,
            maximum_total_open_risk_rate=0.04,
        ),
    )

    entries = result["transactions"].query("action == '仮想エントリー'")
    assert len(entries) == 2
    assert entries.iloc[0]["quantity"] == 6_000
    assert entries.iloc[1]["quantity"] == 2_000


@pytest.mark.parametrize("bad_open", [0, -1, float("inf")])
def test_invalid_entry_open_is_rejected(bad_open):
    prices = _prices()
    prices.loc[prices["price_time"] == _sessions()[2], "open"] = bad_open

    result = simulate_ohlc_portfolio(_signal(), prices)

    assert result["transactions"].empty
    assert set(result["rejected_signals"]["reason"]) == {"missing_entry_open"}


def test_invalid_ohlc_bar_never_creates_an_exit():
    prices = _prices()
    dates = _sessions()
    prices.loc[prices["price_time"] == dates[3], ["open", "high", "low", "close"]] = [
        90,
        80,
        110,
        90,
    ]

    result = simulate_ohlc_portfolio(
        _signal(maximum_holding_days=8),
        prices,
        assumptions=ExecutionAssumptions(fee_rate=0, spread_rate=0, lot_size=1),
        market_impact=MarketImpactAssumptions(base_slippage_rate=0, impact_rate=0),
    )

    exits = result["transactions"].query("action != '仮想エントリー'")
    assert (pd.to_datetime(exits["date"], utc=True) != pd.Timestamp(dates[3], tz="UTC")).all()


def test_walk_forward_windows_never_overlap_training_and_test():
    windows = walk_forward_windows(
        _sessions(), minimum_train_sessions=5, test_sessions=2
    )

    assert windows
    assert all(window.train_end < window.test_start for window in windows)


def test_walk_forward_does_not_evaluate_incomplete_validation_window():
    sessions = _sessions(10)

    windows = walk_forward_windows(
        sessions, minimum_train_sessions=5, test_sessions=3
    )

    assert len(windows) == 1
    assert windows[0].test_end.tz_localize(None) == sessions[7]


def test_frozen_walk_forward_passes_only_test_signals_to_simulator():
    prices = _prices()
    dates = _sessions()
    signals = pd.concat(
        [_signal(entry_location=index) for index in range(2, 10)], ignore_index=True
    )
    observed = []

    def simulator(test_signals, prices_as_of_test):
        observed.append((test_signals.copy(), prices_as_of_test.copy()))
        return {"metrics": {"total_return": 0.0, "maximum_drawdown": 0.0, "closed_trades": 0}}

    report = evaluate_frozen_strategy_walk_forward(
        signals,
        prices,
        simulator,
        minimum_train_sessions=5,
        test_sessions=2,
    )

    assert not report.empty
    for row, (test_signals, prices_as_of_test) in zip(report.itertuples(index=False), observed, strict=True):
        assert (test_signals["signal_date"] >= row.test_start).all()
        assert (test_signals["signal_date"] <= row.test_end).all()
        assert pd.to_datetime(prices_as_of_test["price_time"], utc=True).max() <= row.test_end


def test_validation_window_registry_is_idempotent_and_blocks_reuse(tmp_path):
    registry = tmp_path / "validation-windows.json"
    first = claim_validation_window(
        registry,
        strategy_version="strategy-v1",
        rule_hash="rules-a",
        test_start="2026-01-01",
        test_end="2026-01-31",
    )
    repeated = claim_validation_window(
        registry,
        strategy_version="strategy-v1",
        rule_hash="rules-a",
        test_start="2026-01-01",
        test_end="2026-01-31",
    )

    assert repeated == first
    with pytest.raises(ValidationWindowConflict):
        claim_validation_window(
            registry,
            strategy_version="strategy-v2",
            rule_hash="rules-b",
            test_start="2026-01-15",
            test_end="2026-02-15",
        )


def test_validation_window_registry_allows_new_unseen_period(tmp_path):
    registry = tmp_path / "validation-windows.json"
    claim_validation_window(
        registry,
        strategy_version="strategy-v1",
        rule_hash="rules-a",
        test_start="2026-01-01",
        test_end="2026-01-31",
    )

    later = claim_validation_window(
        registry,
        strategy_version="strategy-v2",
        rule_hash="rules-b",
        test_start="2026-02-01",
        test_end="2026-02-28",
    )

    assert later["immutable"] is True


def test_validation_window_registry_allows_independent_account_tracks(tmp_path):
    registry = tmp_path / "validation-windows.json"
    short = claim_validation_window(
        registry,
        strategy_version="strategy-v1",
        rule_hash="short-rules",
        evaluation_track="short_term",
        test_start="2026-01-01",
        test_end="2026-01-31",
    )
    mid = claim_validation_window(
        registry,
        strategy_version="strategy-v1",
        rule_hash="mid-rules",
        evaluation_track="mid_term",
        test_start="2026-01-01",
        test_end="2026-01-31",
    )

    assert short["evaluation_track"] == "short_term"
    assert mid["evaluation_track"] == "mid_term"


def test_validation_registry_treats_strategy_version_as_frozen_rule_identity(
    tmp_path,
):
    registry = tmp_path / "validation-windows.json"
    claim_validation_window(
        registry,
        strategy_version="strategy-v1",
        rule_hash="same-rules",
        test_start="2026-01-01",
        test_end="2026-01-31",
    )

    with pytest.raises(ValidationWindowConflict, match="different frozen rule"):
        claim_validation_window(
            registry,
            strategy_version="strategy-v2",
            rule_hash="same-rules",
            test_start="2026-01-15",
            test_end="2026-02-15",
        )


def test_validation_registry_rejects_revised_training_input(tmp_path):
    registry = tmp_path / "validation-windows.json"
    common = {
        "strategy_version": "strategy-v1",
        "rule_hash": "rules-a",
        "evaluation_track": "short_term",
        "train_start": "2025-10-01",
        "train_end": "2025-12-31",
        "test_start": "2026-01-01",
        "test_end": "2026-01-31",
        "frozen_rule_hash": "frozen-a",
        "protocol_version": "test-v1",
    }
    claim_validation_window(
        registry, training_input_hash="training-a", **common
    )

    with pytest.raises(ValidationWindowConflict, match="training input changed"):
        claim_validation_window(
            registry, training_input_hash="training-revised", **common
        )


def test_walk_forward_excludes_entry_after_validation_end():
    prices = _prices()
    dates = _sessions()
    signals = pd.DataFrame(
        [
            {
                "signal_date": dates[6],
                "entry_date": dates[7],
                "symbol": "A",
                "side": "long",
            }
        ]
    )
    observed = []

    def simulator(test_signals, _prices_as_of_test):
        observed.append(test_signals.copy())
        return {"metrics": {}}

    evaluate_frozen_strategy_walk_forward(
        signals,
        prices,
        simulator,
        minimum_train_sessions=5,
        test_sessions=2,
    )

    assert observed
    assert observed[0].empty


def test_forward_period_cannot_be_reused_as_historical_validation(tmp_path):
    registry = tmp_path / "validation-windows.json"
    claim_forward_period(
        registry,
        strategy_version="strategy-v1",
        rule_hash="rules-a",
        frozen_rule_hash="frozen-a",
        evaluation_track="short_term",
        validation_end="2026-01-30",
        forward_start="2026-02-02",
        protocol_version="test-v1",
    )

    with pytest.raises(ValidationWindowConflict, match="reserved for forward"):
        claim_validation_window(
            registry,
            strategy_version="strategy-v1",
            rule_hash="rules-a",
            evaluation_track="short_term",
            test_start="2026-02-02",
            test_end="2026-02-27",
        )


def test_forward_period_must_start_after_all_already_observed_data(tmp_path):
    with pytest.raises(
        ValueError, match="after all already-observed data"
    ):
        claim_forward_period(
            tmp_path / "validation-windows.json",
            strategy_version="strategy-v1",
            rule_hash="rules-a",
            frozen_rule_hash="frozen-a",
            evaluation_track="short_term",
            validation_end="2026-01-30",
            observed_through="2026-02-06",
            forward_start="2026-02-02",
            protocol_version="test-v1",
        )


def test_walk_forward_claims_window_before_running_simulator(tmp_path):
    registry = tmp_path / "validation-windows.json"
    prices = _prices()
    signals = pd.concat(
        [_signal(entry_location=index) for index in range(2, 10)], ignore_index=True
    )
    observed_registry_state = []

    def simulator(test_signals, prices_as_of_test):
        observed_registry_state.append(json.loads(registry.read_text(encoding="utf-8")))
        return {
            "metrics": {
                "total_return": 0.0,
                "maximum_drawdown": 0.0,
                "closed_trades": 0,
            }
        }

    report = evaluate_frozen_strategy_walk_forward(
        signals,
        prices,
        simulator,
        minimum_train_sessions=5,
        test_sessions=2,
        validation_registry_path=registry,
        strategy_version="strategy-v1",
        rule_hash="rules-a",
        evaluation_track="short_term",
    )

    assert observed_registry_state
    assert report["validation_claim_id"].notna().all()
    assert all(state for state in observed_registry_state)


def test_forward_shadow_snapshot_is_idempotent_and_contains_warning(tmp_path):
    result = simulate_ohlc_portfolio(_signal(), _prices())
    observed_at = pd.Timestamp("2026-01-20", tz="UTC")

    first = write_forward_shadow_snapshot(tmp_path, result, as_of=observed_at)
    second = write_forward_shadow_snapshot(tmp_path, result, as_of=observed_at)

    assert first == second
    payload = json.loads(first.read_text(encoding="utf-8"))
    assert "実注文" in payload["warning"]
    assert payload["decision_track"] == "delayed_historical"
    assert payload["manifest"]["run_id"] == result["manifest"]["run_id"]


def test_daily_forward_shadow_freezes_first_observation_per_japan_day(tmp_path):
    result = simulate_ohlc_portfolio(_signal(), _prices())
    first = write_forward_shadow_snapshot(
        tmp_path,
        result,
        as_of=pd.Timestamp("2026-01-20T00:00:00Z"),
        daily=True,
    )
    repeated = write_forward_shadow_snapshot(
        tmp_path,
        result,
        as_of=pd.Timestamp("2026-01-20T05:00:00Z"),
        daily=True,
    )

    assert first == repeated
    payload = json.loads(first.read_text(encoding="utf-8"))
    assert first.name == "2026-01-20.json"
    assert payload["observation_cadence"] == "daily"
    assert payload["observation_date_jst"] == "2026-01-20"
    assert "transactions" in payload
    assert "account_state" in payload


def test_daily_forward_shadow_refuses_to_replace_changed_same_day_run(tmp_path):
    first_result = simulate_ohlc_portfolio(_signal(), _prices())
    changed_prices = _prices()
    changed_prices.loc[changed_prices.index[-1], "close"] = 101
    changed_result = simulate_ohlc_portfolio(_signal(), changed_prices)
    observed_at = pd.Timestamp("2026-01-20T00:00:00Z")
    write_forward_shadow_snapshot(
        tmp_path, first_result, as_of=observed_at, daily=True
    )

    with pytest.raises(FileExistsError, match="already frozen"):
        write_forward_shadow_snapshot(
            tmp_path, changed_result, as_of=observed_at, daily=True
        )
