from collections.abc import Mapping
from typing import Protocol

import pandas as pd


class DataProvider(Protocol):
    """Common boundary for market-data providers.

    Phase 1 uses assets, daily prices, and health checks. Fundamentals and
    events intentionally return empty frames until their roadmap phases.
    """

    name: str

    def fetch_assets(self) -> list[dict]: ...

    def fetch_prices(
        self, symbol: str, observation_start: str | None = None
    ) -> tuple[pd.DataFrame, int]: ...

    def fetch_fundamentals(self, symbol: str) -> pd.DataFrame: ...

    def fetch_events(self, symbol: str) -> pd.DataFrame: ...

    def health_check(self) -> Mapping[str, str | bool]: ...
