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


class FundamentalsProvider(Protocol):
    """Phase 3 boundary; implementations must preserve announcement timing."""

    name: str

    def fetch_fundamentals(self, symbol: str, as_of: str | None = None) -> pd.DataFrame: ...


class EtfMetricsProvider(Protocol):
    """Phase 3 boundary for provider-reported ETF metadata and metrics."""

    name: str

    def fetch_etf_metrics(self, symbol: str, as_of: str | None = None) -> pd.DataFrame: ...
