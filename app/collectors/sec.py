"""Read-only SEC EDGAR API client with fair-access safeguards."""

from time import perf_counter
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.core.exceptions import DataProviderError


def _retryable(error: BaseException) -> bool:
    return isinstance(error, httpx.TransportError) or (
        isinstance(error, DataProviderError) and error.retryable
    )


class SecClient:
    provider = "sec_companyfacts"

    def __init__(self) -> None:
        self.settings = get_settings()

    def _headers(self) -> dict[str, str]:
        user_agent = self.settings.sec_user_agent.strip()
        if not user_agent:
            raise DataProviderError(
                "SEC_USER_AGENT is not set; identify the application and contact address",
                category="configuration",
            )
        return {"User-Agent": user_agent, "Accept": "application/json"}

    @retry(
        retry=retry_if_exception(_retryable),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _get_json(self, path: str) -> tuple[dict[str, Any], int]:
        started = perf_counter()
        try:
            with httpx.Client(timeout=self.settings.api_timeout_seconds) as client:
                url = path if path.startswith("http") else f"{self.settings.sec_base_url.rstrip('/')}/{path.lstrip('/')}"
                response = client.get(url, headers=self._headers())
        except httpx.TransportError:
            raise
        if response.status_code >= 400:
            retryable = response.status_code == 429 or response.status_code >= 500
            raise DataProviderError(
                f"SEC rejected request ({response.status_code})",
                category="rate_limited" if response.status_code == 429 else "http_error",
                retryable=retryable,
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise DataProviderError("SEC returned a non-object JSON payload", category="invalid_response")
        return payload, int((perf_counter() - started) * 1000)

    def fetch_companyfacts(self, cik: str) -> tuple[dict[str, Any], int]:
        normalized = str(cik).strip().zfill(10)
        if not normalized.isdigit() or len(normalized) != 10:
            raise DataProviderError("SEC CIK must be a 10-digit number", category="invalid_input")
        return self._get_json(f"api/xbrl/companyfacts/CIK{normalized}.json")

    def fetch_ticker_directory(self) -> tuple[dict[str, Any], int]:
        return self._get_json("https://www.sec.gov/files/company_tickers_exchange.json")
