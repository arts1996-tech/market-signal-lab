import pandas as pd

from app.analysis.backtest import forward_returns
from app.analysis.regression import run_ols
from app.analysis.scoring import score_mid_term, score_short_term


def test_regression_returns_coefficients():
    index = pd.RangeIndex(20)
    features = pd.DataFrame({"x1": range(20), "x2": [1] * 20}, index=index)
    target = pd.Series([value * 2 + 1 for value in range(20)], index=index)

    result = run_ols(features, target)

    assert result["status"] == "ok"
    assert result["sample_size"] == 20
    assert "x1" in result["coefficients"]


def test_short_and_mid_scores_are_bounded_and_versioned():
    short = score_short_term({"trend": 1, "volume": 1, "event_risk": 0})
    mid = score_mid_term({"growth": 1, "risk": 0})

    assert 0 <= short.score <= 100
    assert short.rule_version.startswith("short-term")
    assert 0 <= mid.score <= 100
    assert mid.rule_version.startswith("mid-term")


def test_forward_returns_only_use_future_rows_after_signal():
    dates = pd.date_range("2024-01-01", periods=6)
    close = pd.Series([100, 99, 110, 120, 130, 140], index=dates)

    result = forward_returns(close, pd.Index([dates[1]]), horizons=[1, 3])

    assert result.loc[0, "entry_price"] == 99
    assert result.loc[0, "return_1d"] == 110 / 99 - 1
    assert result.loc[0, "return_3d"] == 130 / 99 - 1

