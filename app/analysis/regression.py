import numpy as np
import pandas as pd
import statsmodels.api as sm
import warnings
from statsmodels.tsa.stattools import grangercausalitytests


def run_ols(features: pd.DataFrame, target: pd.Series) -> dict:
    data = pd.concat([features, target.rename("target")], axis=1).dropna()
    if len(data) < max(10, len(features.columns) + 2):
        return {"status": "insufficient_data", "sample_size": len(data)}
    x = sm.add_constant(data[features.columns])
    model = sm.OLS(data["target"], x).fit()
    confidence_intervals = model.conf_int()
    return {
        "status": "ok",
        "sample_size": int(model.nobs),
        "r_squared": float(model.rsquared),
        "adjusted_r_squared": float(model.rsquared_adj),
        "coefficients": {key: float(value) for key, value in model.params.items()},
        "p_values": {key: float(value) for key, value in model.pvalues.items()},
        "confidence_intervals_95": {
            key: [float(bounds.iloc[0]), float(bounds.iloc[1])]
            for key, bounds in confidence_intervals.iterrows()
        },
    }


def rolling_ols(features: pd.DataFrame, target: pd.Series, window: int) -> pd.DataFrame:
    """Fit a trailing-window OLS at each point, without using future observations."""
    data = pd.concat([features, target.rename("target")], axis=1).dropna().sort_index()
    columns = ["period_end", "sample_size", "r_squared", *features.columns]
    if window < max(10, len(features.columns) + 2) or len(data) < window:
        return pd.DataFrame(columns=columns)
    rows: list[dict] = []
    for end in range(window, len(data) + 1):
        sample = data.iloc[end - window : end]
        result = run_ols(sample[features.columns], sample["target"])
        if result["status"] != "ok":
            continue
        rows.append(
            {
                "period_end": sample.index[-1],
                "sample_size": result["sample_size"],
                "r_squared": result["r_squared"],
                **{
                    column: result["coefficients"].get(column)
                    for column in features.columns
                },
            }
        )
    return pd.DataFrame(rows, columns=columns)


def run_granger_test(feature: pd.Series, target: pd.Series, max_lag: int = 5) -> dict:
    """Test predictive precedence only; this does not establish causality."""
    data = pd.concat([target.rename("target"), feature.rename("feature")], axis=1).dropna()
    minimum_samples = max(30, max_lag * 5 + 1)
    if len(data) < minimum_samples:
        return {"status": "insufficient_data", "sample_size": len(data), "max_lag": max_lag}
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="verbose is deprecated", category=FutureWarning)
            results = grangercausalitytests(data[["target", "feature"]], maxlag=max_lag, verbose=False)
    except (ValueError, ZeroDivisionError, np.linalg.LinAlgError) as exc:
        return {"status": "not_estimable", "sample_size": len(data), "max_lag": max_lag, "message": str(exc)}
    lag_results = {
        lag: {
            "ssr_ftest_p_value": float(values[0]["ssr_ftest"][1]),
            "ssr_ftest_statistic": float(values[0]["ssr_ftest"][0]),
        }
        for lag, values in results.items()
    }
    return {
        "status": "ok",
        "sample_size": len(data),
        "max_lag": max_lag,
        "lag_results": lag_results,
        "minimum_p_value": min(result["ssr_ftest_p_value"] for result in lag_results.values()),
    }
