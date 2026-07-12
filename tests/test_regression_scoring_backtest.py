import pandas as pd
import pytest
import numpy as np

from app.analysis.backtest import forward_returns
from app.analysis.regression import rolling_ols, run_granger_test, run_ols, walk_forward_ols
from app.analysis.scoring import score_mid_term, score_short_term


def test_regression_returns_coefficients():
    index = pd.RangeIndex(20)
    features = pd.DataFrame({"x1": range(20), "x2": [1] * 20}, index=index)
    target = pd.Series([value * 2 + 1 for value in range(20)], index=index)

    result = run_ols(features, target)

    assert result["status"] == "ok"
    assert result["sample_size"] == 20
    assert "x1" in result["coefficients"]
    assert "x1" in result["confidence_intervals_95"]


def test_rolling_ols_uses_only_trailing_window_observations():
    index = pd.date_range("2024-01-01", periods=15, tz="UTC")
    features = pd.DataFrame({"us_return": range(15)}, index=index)
    target = pd.Series([value * 0.5 for value in range(15)], index=index)

    rolling = rolling_ols(features, target, window=10)

    assert len(rolling) == 6
    assert rolling.iloc[0]["period_end"] == index[9]
    assert rolling.iloc[-1]["us_return"] == pytest.approx(0.5)


def test_granger_test_reports_predictive_precedence_without_causality_claim():
    rng = np.random.default_rng(42)
    index = pd.date_range("2024-01-01", periods=40, tz="UTC")
    feature = pd.Series(rng.normal(size=40), index=index)
    target = feature.shift(1).fillna(0) * 0.4 + rng.normal(scale=0.1, size=40)

    result = run_granger_test(feature, target, max_lag=3)

    assert result["status"] == "ok"
    assert result["sample_size"] == 40
    assert set(result["lag_results"]) == {1, 2, 3}
    assert all("adjusted_p_value" in row for row in result["lag_results"].values())


def test_walk_forward_ols_never_uses_the_forecast_row_in_training():
    index = pd.date_range("2024-01-01", periods=20, tz="UTC")
    features = pd.DataFrame({"x": range(20)}, index=index)
    target = pd.Series([value * 2 + 1 for value in range(20)], index=index)

    result = walk_forward_ols(features, target, min_train_size=10)

    assert len(result) == 10
    assert result.iloc[0]["train_size"] == 10
    assert result.iloc[0]["period_end"] == index[10]


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
