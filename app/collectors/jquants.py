from datetime import UTC, datetime
from time import perf_counter, sleep
from typing import Any

import httpx
import pandas as pd
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.core.exceptions import DataProviderError
from app.core.security import redact_sensitive_text


class JQuantsClient:
    provider = "jquants"
    listed_info_endpoints = (
        "/v2/equities/master",
        "/v2/listed/info",
        "/v1/listed/info",
    )

    def __init__(self) -> None:
        self.settings = get_settings()

    def _headers(self) -> dict[str, str]:
        if not self.settings.jquants_api_key:
            raise DataProviderError("JQUANTS_API_KEY is not set")
        return {"x-api-key": self.settings.jquants_api_key}

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def fetch_daily_bars(
        self,
        code: str,
        date: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> tuple[pd.DataFrame, int]:
        params: dict[str, Any] = {"code": code}
        if date:
            params["date"] = date
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date

        started = perf_counter()
        frames = []
        with httpx.Client(timeout=self.settings.api_timeout_seconds) as client:
            while True:
                response = client.get(
                    f"{self.settings.jquants_base_url}/v2/equities/bars/daily",
                    params=params,
                    headers=self._headers(),
                )
                if response.status_code >= 400:
                    raise DataProviderError(
                        build_http_error_message(response),
                        category=jquants_error_category(response.status_code),
                        retryable=response.status_code == 429 or response.status_code >= 500,
                    )
                payload = response.json()
                frames.append(parse_daily_bars_response(code, payload))
                pagination_key = payload.get("pagination_key") or payload.get("paginationKey")
                if not pagination_key:
                    break
                params = {**params, "pagination_key": pagination_key}
        latency_ms = int((perf_counter() - started) * 1000)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(), latency_ms

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def fetch_financial_summary(
        self, code: str, disclosed_from: str | None = None, disclosed_to: str | None = None
    ) -> tuple[pd.DataFrame, int]:
        """Fetch provider-reported financial summary; no statement inference."""
        params: dict[str, Any] = {"code": code}
        if disclosed_from:
            params["from"] = disclosed_from
        if disclosed_to:
            params["to"] = disclosed_to
        started = perf_counter()
        with httpx.Client(timeout=self.settings.api_timeout_seconds) as client:
            response = client.get(
                f"{self.settings.jquants_base_url}/v2/fins/summary",
                params=params,
                headers=self._headers(),
            )
            if response.status_code >= 400:
                raise DataProviderError(
                    build_http_error_message(response),
                    category=jquants_error_category(response.status_code),
                    retryable=response.status_code == 429 or response.status_code >= 500,
                )
            payload = response.json()
        records = find_financial_summary_records(payload)
        return pd.DataFrame(records), int((perf_counter() - started) * 1000)

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def fetch_listed_info(self, date: str | None = None) -> tuple[list[dict[str, Any]], int, str]:
        params: dict[str, Any] = {}
        if date:
            params["date"] = date

        started = perf_counter()
        last_error: DataProviderError | None = None
        with httpx.Client(timeout=self.settings.api_timeout_seconds) as client:
            for endpoint in self.listed_info_endpoints:
                response = client.get(
                    f"{self.settings.jquants_base_url}{endpoint}",
                    params=params,
                    headers=self._headers(),
                )
                if response.status_code < 400:
                    latency_ms = int((perf_counter() - started) * 1000)
                    return parse_listed_info_response(response.json()), latency_ms, endpoint
                last_error = DataProviderError(
                    build_http_error_message(response),
                    category=jquants_error_category(response.status_code),
                    retryable=response.status_code == 429 or response.status_code >= 500,
                )
                if last_error.retryable:
                    raise last_error
        latency_ms = int((perf_counter() - started) * 1000)
        message = str(last_error) if last_error else "J-Quants listed info request failed"
        raise DataProviderError(f"{message}. tried_endpoints={list(self.listed_info_endpoints)} latency_ms={latency_ms}")

    def respect_free_plan_rate_limit(self) -> None:
        if self.settings.jquants_min_request_interval_seconds:
            sleep(self.settings.jquants_min_request_interval_seconds)


def parse_daily_bars_response(code: str, payload: dict[str, Any] | list[dict[str, Any]]) -> pd.DataFrame:
    records = find_record_list(payload)
    if records is None:
        raise DataProviderError("Unexpected J-Quants daily bars response")
    if not records:
        return pd.DataFrame()

    rows = []
    fetched_at = datetime.now(UTC)
    for item in records:
        if not isinstance(item, dict):
            continue
        date_value = pick_value(item, ["Date", "date", "LocalCodeDate", "localCodeDate", "baseDate", "base_date"])
        adjusted_values = {
            "open": pick_value(item, ["AdjustmentOpen", "adjustmentOpen", "adjustment_open", "AdjO"]),
            "high": pick_value(item, ["AdjustmentHigh", "adjustmentHigh", "adjustment_high", "AdjH"]),
            "low": pick_value(item, ["AdjustmentLow", "adjustmentLow", "adjustment_low", "AdjL"]),
            "close": pick_value(item, ["AdjustmentClose", "adjustmentClose", "adjustment_close", "AdjC"]),
        }
        raw_values = {
            "open": pick_value(item, ["Open", "open", "O"]),
            "high": pick_value(item, ["High", "high", "H"]),
            "low": pick_value(item, ["Low", "low", "L"]),
            "close": pick_value(item, ["Close", "close", "C"]),
        }
        use_adjusted = all(value not in (None, "") for value in adjusted_values.values())
        close_value = raw_values["close"]
        if not date_value or close_value in (None, ""):
            continue
        price_time = pd.to_datetime(date_value).to_pydatetime().replace(tzinfo=UTC)
        rows.append(
            {
                "symbol": code,
                "price_time": price_time,
                "session_date": price_time.date(),
                "open": to_float_or_none(raw_values["open"]),
                "high": to_float_or_none(raw_values["high"]),
                "low": to_float_or_none(raw_values["low"]),
                "close": float(close_value),
                "adjusted_close": to_float_or_none(adjusted_values["close"]),
                "adjusted_open": to_float_or_none(adjusted_values["open"]),
                "adjusted_high": to_float_or_none(adjusted_values["high"]),
                "adjusted_low": to_float_or_none(adjusted_values["low"]),
                "adjusted_volume": to_float_or_none(
                    pick_value(item, ["AdjustmentVolume", "adjustmentVolume", "adjustment_volume", "AdjVo"])
                ),
                "adjustment_factor": to_float_or_none(
                    pick_value(item, ["AdjustmentFactor", "adjustmentFactor", "adjustment_factor", "AdjFactor"])
                ),
                "volume": to_float_or_none(
                    pick_value(item, ["Volume", "volume", "Vo", "AdjustmentVolume", "adjustmentVolume", "AdjVo"])
                ),
                "source": "jquants",
                "source_symbol": code,
                "fetched_at": fetched_at,
                "available_at": fetched_at,
                "data_quality_status": "complete_adjusted_ohlcv" if use_adjusted else "complete_raw_ohlcv",
                "price_basis": "raw_ohlcv_with_adjusted" if use_adjusted else "provider_reported_ohlcv",
            }
        )
    if not rows and all_records_are_null_price_observations(records):
        return pd.DataFrame()
    if not rows:
        first_keys = sorted(records[0].keys()) if isinstance(records[0], dict) else []
        raise DataProviderError(f"No parsable J-Quants daily bars. first_record_keys={first_keys}")
    return pd.DataFrame(rows)


def all_records_are_null_price_observations(records: list[dict[str, Any]]) -> bool:
    """Recognize provider-reported no-trade rows without hiding malformed payloads."""
    price_keys = [
        "Close", "close", "C", "AdjustmentClose", "adjustmentClose", "adjustment_close", "AdjC"
    ]
    date_keys = ["Date", "date", "LocalCodeDate", "localCodeDate", "baseDate", "base_date"]
    return bool(records) and all(
        isinstance(record, dict)
        and pick_value(record, date_keys)
        and any(key in record for key in price_keys)
        and pick_value(record, price_keys) in (None, "")
        for record in records
    )


def find_record_list(payload: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    if isinstance(payload, list):
        return payload
    for key in ["daily_quotes", "daily_bars", "dailyBars", "bars", "quotes", "data", "items", "rows"]:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return None


def parse_listed_info_response(payload: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = find_listed_info_records(payload)
    if records is None:
        raise DataProviderError("Unexpected J-Quants listed info response")

    assets = []
    for item in records:
        if not isinstance(item, dict):
            continue
        code = pick_value(item, ["Code", "code", "LocalCode", "localCode"])
        if not code:
            continue
        name = pick_value(item, ["CompanyName", "companyName", "Name", "name", "IssueName", "issueName"])
        market = pick_value(item, ["MarketCodeName", "marketCodeName", "MarketName", "marketName"])
        sector_17 = pick_value(item, ["Sector17CodeName", "sector17CodeName"])
        sector_33 = pick_value(item, ["Sector33CodeName", "sector33CodeName"])
        effective_date = pick_value(item, ["Date", "date", "BaseDate", "baseDate"])
        listed_on = pick_value(item, ["ListingDate", "listingDate", "ListedDate", "listedDate"])
        delisted_on = pick_value(item, ["DelistingDate", "delistingDate", "DelistedDate", "delistedDate"])
        assets.append(
            {
                "symbol": str(code),
                "name": str(name or f"J-Quants {code}"),
                "asset_type": classify_jquants_asset_type(item),
                "currency": "JPY",
                "exchange": "JPX",
                "source": "jquants",
                "metadata_json": {
                    "market": market,
                    "sector_17": sector_17,
                    "sector_33": sector_33,
                    "lifecycle": {
                        "effective_date": effective_date,
                        "listed_on": listed_on,
                        "delisted_on": delisted_on,
                    },
                    "raw": item,
                    "free_plan_note": "J-Quants Free plan data is delayed by 12 weeks.",
                },
            }
        )
    if not assets and records:
        first_keys = sorted(records[0].keys()) if isinstance(records[0], dict) else []
        raise DataProviderError(f"No parsable J-Quants listed info. first_record_keys={first_keys}")
    return assets


def find_listed_info_records(payload: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    if isinstance(payload, list):
        return payload
    for key in ["master", "info", "listed_info", "listedInfo", "data", "items", "rows"]:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return None


def find_financial_summary_records(payload: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    for key in ["financial_summary", "financialSummary", "fins", "data", "items", "rows"]:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    raise DataProviderError("Unexpected J-Quants financial summary response")


def classify_jquants_asset_type(item: dict[str, Any]) -> str:
    name_parts = [
        str(pick_value(item, ["CompanyName", "companyName", "Name", "name", "IssueName", "issueName"]) or ""),
        str(pick_value(item, ["Sector17CodeName", "sector17CodeName"]) or ""),
        str(pick_value(item, ["Sector33CodeName", "sector33CodeName"]) or ""),
    ]
    text = " ".join(name_parts).lower()
    if "etf" in text or "etn" in text or "投信" in text or "上場投資信託" in text:
        return "etf"
    return "stock"


def pick_value(item: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in item:
            return item[key]
    return None


def to_float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def build_http_error_message(response: httpx.Response) -> str:
    body = redact_sensitive_text(response.text.strip().replace("\n", " "))
    if len(body) > 240:
        body = f"{body[:240]}..."
    message = f"J-Quants rejected request ({response.status_code})"
    if response.status_code == 400:
        message += ". Check that code/date are valid for the Free plan range"
    if body:
        message += f": {body}"
    return message


def jquants_error_category(status_code: int) -> str:
    if status_code in {401, 403}:
        return "authentication_error"
    if status_code == 429:
        return "rate_limited"
    if status_code >= 500:
        return "provider_unavailable"
    if status_code == 400:
        return "invalid_request"
    return "provider_rejected_request"
