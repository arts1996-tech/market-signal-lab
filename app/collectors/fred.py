from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import httpx
import pandas as pd
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.core.exceptions import DataProviderError


FRED_INDEX_SERIES = {
    "NASDAQCOM": "NASDAQ Composite",
    "DJIA": "Dow Jones Industrial Average",
    "SP500": "S&P 500",
    "NIKKEI225": "Nikkei 225",
    "DEXJPUS": "USD/JPY",
}


class FredClient:
    provider = "fred"

    def __init__(self) -> None:
        self.settings = get_settings()

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, DataProviderError)),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def fetch_series(self, series_id: str, observation_start: str | None = None) -> tuple[pd.DataFrame, int]:
        if not self.settings.fred_api_key:
            raise DataProviderError("FRED_API_KEY is not set")

        params: dict[str, Any] = {
            "series_id": series_id,
            "api_key": self.settings.fred_api_key,
            "file_type": "json",
            "sort_order": "asc",
        }
        if observation_start:
            params["observation_start"] = observation_start

        started = perf_counter()
        with httpx.Client(timeout=self.settings.api_timeout_seconds) as client:
            response = client.get(f"{self.settings.fred_base_url}/series/observations", params=params)
            response.raise_for_status()
        latency_ms = int((perf_counter() - started) * 1000)
        payload = response.json()
        observations = payload.get("observations", [])
        if not isinstance(observations, list):
            raise DataProviderError(f"Unexpected FRED response for {series_id}")

        rows = []
        for item in observations:
            raw_value = item.get("value")
            if raw_value in (None, "."):
                continue
            price_date = pd.to_datetime(item["date"]).to_pydatetime().replace(tzinfo=UTC)
            fetched_at = datetime.now(UTC)
            rows.append(
                {
                    "symbol": series_id,
                    "price_time": price_date,
                    "session_date": price_date.date(),
                    "open": None,
                    "high": None,
                    "low": None,
                    "close": float(raw_value),
                    "adjusted_close": float(raw_value),
                    "adjusted_open": None,
                    "adjusted_high": None,
                    "adjusted_low": None,
                    "adjusted_volume": None,
                    "adjustment_factor": None,
                    "volume": None,
                    "source": self.provider,
                    "source_symbol": series_id,
                    "fetched_at": fetched_at,
                    "available_at": fetched_at,
                    "data_quality_status": "close_only",
                    "price_basis": "provider_reported_close_only",
                }
            )
        return pd.DataFrame(rows), latency_ms
