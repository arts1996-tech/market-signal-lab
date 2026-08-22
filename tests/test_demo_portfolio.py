import pandas as pd
import pytest

from app.analysis.demo_portfolio import run_demo_portfolio_environment


def test_demo_portfolio_uses_isolated_deterministic_inputs_and_accounts():
    result = run_demo_portfolio_environment()
    repeated = run_demo_portfolio_environment()

    assert result["mode"] == "demo_only"
    assert set(result["prices"]["source"]) == {"demo"}
    assert result["prices"]["synthetic"].all()
    assert set(result["news"]["source"]) == {"demo_scenario"}
    assert result["news"]["synthetic"].all()
    assert set(result["accounts"]) == {"short_term", "mid_term"}
    assert result["assumptions"]["initial_cash_each"] == 2_500_000
    assert result["assumptions"]["tax_rate"] == 0.0

    for account_name, account in result["accounts"].items():
        assert account["initial_cash"] == 2_500_000
        assert account["account_name"] == account_name
        assert account["cash"] >= 0
        assert account["equity"] == pytest.approx(
            account["cash"] + account["positions"].get("market_value", pd.Series(dtype=float)).sum()
        )
        assert account["equity"] == pytest.approx(repeated["accounts"][account_name]["equity"])


def test_demo_portfolio_never_uses_execution_day_information_for_decisions():
    result = run_demo_portfolio_environment()

    for account in result["accounts"].values():
        transactions = account["transactions"]
        assert not transactions.empty
        decision_times = pd.to_datetime(transactions["decision_as_of"], utc=True)
        execution_times = pd.to_datetime(transactions["date"], utc=True)
        assert (decision_times < execution_times).all()


def test_demo_exit_labels_follow_conditions_instead_of_profit_sign():
    result = run_demo_portfolio_environment()

    exits = pd.concat(
        [
            account["transactions"].query("action != '仮想エントリー'")
            for account in result["accounts"].values()
        ],
        ignore_index=True,
    )
    assert not exits.empty
    for row in exits.itertuples(index=False):
        if row.reason.startswith("利益確定条件成立"):
            assert row.action == "利益確定"
        elif row.reason.startswith("損切り条件成立"):
            assert row.action == "損切り"
        else:
            assert row.reason == "最大保有期間到達"
            assert row.action == "保有期限決済"


def test_demo_portfolio_uses_shared_ohlc_engine_and_audit_manifest():
    result = run_demo_portfolio_environment()

    for account in result["accounts"].values():
        assert account["manifest"]["execution_version"] == "ohlc-next-open-conservative-v3"
        assert account["manifest"]["run_id"]
        assert not account["decision_cards"].empty
        assert account["decision_cards"]["human_review_required"].all()
        assert account["market_impact"].require_volume is True


def test_demo_backtest_can_explicitly_claim_validation_windows(tmp_path):
    registry = tmp_path / "validation-windows.json"

    result = run_demo_portfolio_environment(validation_registry_path=registry)

    assert registry.exists()
    for account in result["accounts"].values():
        assert account["walk_forward"]["validation_claim_id"].notna().all()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"initial_cash": 0},
        {"fee_rate": -0.01},
        {"spread_rate": -0.01},
        {"lot_size": 0},
    ],
)
def test_demo_portfolio_rejects_invalid_execution_assumptions(kwargs):
    with pytest.raises(ValueError):
        run_demo_portfolio_environment(**kwargs)
