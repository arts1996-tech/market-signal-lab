import pandas as pd
import pytest

from app.backtest.corporate_actions import CorporateActionPolicy
from app.backtest.ohlc import simulate_ohlc_portfolio
from app.backtest.tax_accounting import (
    TAX_ACCOUNTING_VERSION,
    TaxAccountingPolicy,
)


def _prices() -> pd.DataFrame:
    sessions = pd.date_range("2026-01-05", periods=4, freq="B", tz="UTC")
    return pd.DataFrame(
        {
            "price_time": sessions,
            "symbol": "A",
            "open": [100.0, 100.0, 101.0, 108.0],
            "high": [101.0, 102.0, 110.0, 109.0],
            "low": [99.0, 99.0, 100.0, 107.0],
            "close": [100.0, 101.0, 108.0, 108.0],
            "volume": [1_000_000.0] * 4,
            "source": ["test"] * 4,
        }
    )


def _signals() -> pd.DataFrame:
    sessions = pd.date_range("2026-01-05", periods=4, freq="B", tz="UTC")
    return pd.DataFrame(
        [
            {
                "signal_date": sessions[0],
                "entry_date": sessions[1],
                "symbol": "A",
                "side": "long",
                "score": 80,
                "minimum_score": 70,
                "take_profit": 0.08,
                "stop_loss": -0.05,
                "maximum_holding_days": 10,
            }
        ]
    )


def test_pretax_policy_discloses_unmodeled_tax_scope():
    disclosure = TaxAccountingPolicy().disclosure()

    assert disclosure["version"] == TAX_ACCOUNTING_VERSION
    assert disclosure["evaluation_basis"] == "pretax"
    assert disclosure["capital_gains_tax_rate"] == 0
    assert disclosure["dividend_tax_rate"] == 0
    assert disclosure["estimated_tax"] == 0
    assert disclosure["tax_model_status"] == "not_implemented"
    assert disclosure["loss_offsetting"] == "not_modeled"


@pytest.mark.parametrize(
    "changes",
    [
        {"capital_gains_tax_rate": 0.1},
        {"dividend_tax_rate": 0.1},
        {"evaluation_basis": "after_tax"},
        {"account_type": "taxable"},
        {"loss_offsetting": "simple"},
        {"withholding": "simple"},
        {"tax_model_status": "implemented"},
    ],
)
def test_pretax_policy_rejects_unapproved_tax_model(changes):
    with pytest.raises(ValueError):
        TaxAccountingPolicy(**changes)


def test_legacy_dividend_tax_configuration_is_rejected():
    with pytest.raises(ValueError, match="pretax"):
        CorporateActionPolicy(dividend_tax_rate=0.1)


def test_ohlc_result_and_transactions_are_explicitly_pretax():
    result = simulate_ohlc_portfolio(_signals(), _prices())

    assert result["tax_summary"]["evaluation_basis"] == "pretax"
    assert result["tax_summary"]["tax_model_status"] == "not_implemented"
    assert result["manifest"]["tax_accounting_policy"]["version"] == (
        TAX_ACCOUNTING_VERSION
    )
    assert result["manifest"]["execution_version"] == (
        "ohlc-next-open-conservative-v6"
    )
    assert set(result["transactions"]["tax"]) == {0.0}
    assert set(result["transactions"]["tax_accounting_version"]) == {
        TAX_ACCOUNTING_VERSION
    }
    assert set(result["transactions"]["tax_evaluation_basis"]) == {"pretax"}
    assert set(result["decision_cards"]["tax_accounting_version"]) == {
        TAX_ACCOUNTING_VERSION
    }
