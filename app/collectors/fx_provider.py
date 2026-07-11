from typing import Protocol

import pandas as pd


class FxProvider(Protocol):
    provider: str

    def fetch_usd_jpy(self, observation_start: str | None = None) -> tuple[pd.DataFrame, int]:
        """Return USD/JPY observations without filling missing market dates."""


class FredUsdJpyProvider:
    provider = "fred"

    def __init__(self, fred_client):
        self.fred_client = fred_client

    def fetch_usd_jpy(self, observation_start: str | None = None) -> tuple[pd.DataFrame, int]:
        return self.fred_client.fetch_series("DEXJPUS", observation_start=observation_start)

