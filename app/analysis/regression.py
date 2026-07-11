import pandas as pd
import statsmodels.api as sm


def run_ols(features: pd.DataFrame, target: pd.Series) -> dict:
    data = pd.concat([features, target.rename("target")], axis=1).dropna()
    if len(data) < max(10, len(features.columns) + 2):
        return {"status": "insufficient_data", "sample_size": len(data)}
    x = sm.add_constant(data[features.columns])
    model = sm.OLS(data["target"], x).fit()
    return {
        "status": "ok",
        "sample_size": int(model.nobs),
        "r_squared": float(model.rsquared),
        "coefficients": {key: float(value) for key, value in model.params.items()},
        "p_values": {key: float(value) for key, value in model.pvalues.items()},
    }

