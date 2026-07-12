from collections.abc import Mapping

import pandas as pd

from app.collectors.fred import FRED_INDEX_SERIES, FredClient
from app.database.repositories import ASSET_DEFINITIONS


class FredMarketProvider:
    """FRED adapter implementing the common provider boundary."""

    name = "fred"

    def __init__(self, client: FredClient | None = None) -> None:
        self.client = client or FredClient()

    def fetch_assets(self) -> list[dict]:
        return [dict(asset) for asset in ASSET_DEFINITIONS if asset["source"] == self.name]

    def fetch_prices(
        self, symbol: str, observation_start: str | None = None
    ) -> tuple[pd.DataFrame, int]:
        if symbol not in FRED_INDEX_SERIES:
            raise ValueError(f"Unsupported FRED series: {symbol}")
        return self.client.fetch_series(symbol, observation_start=observation_start)

    def fetch_fundamentals(self, symbol: str) -> pd.DataFrame:
        return pd.DataFrame()

    def fetch_events(self, symbol: str) -> pd.DataFrame:
        return pd.DataFrame()

    def health_check(self) -> Mapping[str, str | bool]:
        configured = bool(self.client.settings.fred_api_key)
        return {
            "provider": self.name,
            "configured": configured,
            "status": "ready" if configured else "missing_api_key",
        }
