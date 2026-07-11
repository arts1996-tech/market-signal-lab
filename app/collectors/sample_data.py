from datetime import UTC, datetime

import numpy as np
import pandas as pd


def generate_sample_market_data(periods: int = 320) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    dates = pd.bdate_range(end=pd.Timestamp.now(tz=UTC).normalize(), periods=periods)

    us_base = rng.normal(0.00035, 0.012, periods)
    dow_noise = rng.normal(0, 0.004, periods)
    sp_noise = rng.normal(0, 0.003, periods)
    nikkei_noise = rng.normal(0, 0.009, periods)
    fx_returns = rng.normal(0.00005, 0.004, periods)

    returns = {
        "NASDAQCOM": us_base + rng.normal(0, 0.005, periods),
        "DJIA": us_base * 0.72 + dow_noise,
        "SP500": us_base * 0.88 + sp_noise,
        "NIKKEI225": np.roll(us_base, 1) * 0.42 + fx_returns * 0.35 + nikkei_noise,
        "DEXJPUS": fx_returns,
    }
    starting_values = {
        "NASDAQCOM": 15000,
        "DJIA": 38000,
        "SP500": 5000,
        "NIKKEI225": 39000,
        "DEXJPUS": 150,
    }

    rows = []
    fetched_at = datetime.now(UTC)
    for symbol, series_returns in returns.items():
        values = starting_values[symbol] * np.cumprod(1 + series_returns)
        for price_time, close in zip(dates, values, strict=True):
            rows.append(
                {
                    "symbol": symbol,
                    "price_time": price_time.to_pydatetime(),
                    "open": None,
                    "high": None,
                    "low": None,
                    "close": float(close),
                    "adjusted_close": float(close),
                    "volume": None,
                    "source": "sample",
                    "fetched_at": fetched_at,
                }
            )
    return pd.DataFrame(rows)

