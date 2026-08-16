"""Audited single-target collection services for phase-3 fundamentals and ETF data."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.analysis.etf_metrics import normalize_etf_metrics
from app.analysis.fundamentals import normalize_financial_summary
from app.analysis.sec_fundamentals import normalize_sec_companyfacts
from app.collectors.jquants import JQuantsClient
from app.collectors.sec import SecClient
from app.core.exceptions import DataProviderError
from app.database.models import Asset
from app.database.repositories import (
    insert_api_fetch_log,
    insert_etf_metric_snapshots,
    insert_fundamental_snapshots,
    insert_job_run,
)


FUNDAMENTAL_FIELDS = (
    "sales",
    "operating_profit",
    "net_income",
    "eps",
    "equity",
    "total_assets",
    "operating_cashflow",
)
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")
SOURCE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


class CollectionInputError(ValueError):
    def __init__(self, classification: str):
        super().__init__(classification)
        self.classification = classification


def normalize_symbol(value: str) -> str:
    symbol = str(value or "").strip().upper()
    if not SYMBOL_PATTERN.fullmatch(symbol):
        raise CollectionInputError("invalid_symbol")
    return symbol


def normalize_cik(value: str) -> str:
    cik = str(value or "").strip()
    if not cik.isdigit() or len(cik) > 10:
        raise CollectionInputError("invalid_cik")
    return cik.zfill(10)


def normalize_source(value: str) -> str:
    source = str(value or "").strip().lower()
    if not SOURCE_PATTERN.fullmatch(source):
        raise CollectionInputError("invalid_source")
    return source


def validate_date_range(from_date: str | None, to_date: str | None) -> None:
    try:
        start = date.fromisoformat(from_date) if from_date else None
        end = date.fromisoformat(to_date) if to_date else None
    except ValueError as exc:
        raise CollectionInputError("invalid_date") from exc
    if start and end and start > end:
        raise CollectionInputError("invalid_date_range")


def classify_collection_error(exc: Exception) -> tuple[str, bool]:
    if isinstance(exc, CollectionInputError):
        return exc.classification, False
    if isinstance(exc, DataProviderError):
        return exc.category, exc.retryable
    if isinstance(exc, httpx.TimeoutException):
        return "timeout", True
    if isinstance(exc, httpx.TransportError):
        return "network_error", True
    if isinstance(exc, json.JSONDecodeError):
        return "invalid_json", False
    if isinstance(exc, OSError):
        return "input_file_error", False
    if isinstance(exc, SQLAlchemyError):
        return "database_error", True
    return "unexpected_error", False


def _python_value(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value.item() if hasattr(value, "item") else value


def _asset(session: Session, symbol: str) -> Asset:
    asset = session.scalar(select(Asset).where(Asset.symbol == symbol))
    if asset is None:
        raise CollectionInputError("asset_not_found")
    return asset


def _fundamental_payload(
    asset: Asset,
    frame: pd.DataFrame,
    *,
    source: str,
    fetched_at: datetime,
) -> tuple[list[dict[str, Any]], int]:
    payload: list[dict[str, Any]] = []
    rejected = 0
    for row in frame.to_dict("records"):
        disclosed_at = _python_value(row.get("disclosed_at"))
        period_end = _python_value(row.get("period_end"))
        if disclosed_at is None or period_end is None:
            rejected += 1
            continue
        values = {field: _python_value(row.get(field)) for field in FUNDAMENTAL_FIELDS}
        details = {
            "provider": source,
            **values,
            "book_value_per_share": _python_value(row.get("book_value_per_share")),
            "currency": _python_value(row.get("currency")),
            "unit": _python_value(row.get("unit")),
        }
        payload.append(
            {
                "asset_id": asset.id,
                "disclosed_at": disclosed_at,
                "period_end": period_end,
                "source": source,
                "fetched_at": fetched_at,
                "details": details,
                **values,
            }
        )
    return payload, rejected


def _record_result(
    session: Session,
    *,
    job_name: str,
    provider: str,
    endpoint: str,
    symbol: str | None,
    started_at: datetime,
    result: dict[str, Any],
) -> dict[str, Any]:
    finished_at = datetime.now(UTC)
    insert_api_fetch_log(
        session,
        provider=provider,
        endpoint=endpoint,
        status=result["status"],
        asset_symbol=symbol,
        fetched_at=finished_at,
        latency_ms=result.get("latency_ms"),
        message=result["classification"],
    )
    insert_job_run(
        session,
        job_name=job_name,
        status=result["status"],
        started_at=started_at,
        finished_at=finished_at,
        details={key: value for key, value in result.items() if key != "fetched_at"},
    )
    session.commit()
    return result


def _record_failure(
    session: Session,
    *,
    job_name: str,
    provider: str,
    endpoint: str,
    symbol: str | None,
    started_at: datetime,
    exc: Exception,
) -> dict[str, Any]:
    session.rollback()
    classification, retryable = classify_collection_error(exc)
    finished_at = datetime.now(UTC)
    result = {
        "status": "error",
        "classification": classification,
        "provider": provider,
        "symbol": symbol,
        "saved_rows": 0,
        "retryable": retryable,
        "error_type": exc.__class__.__name__,
        "fetched_at": finished_at.isoformat(),
        "writes_database": False,
    }
    insert_api_fetch_log(
        session,
        provider=provider,
        endpoint=endpoint,
        status="retry_pending" if retryable else "error",
        asset_symbol=symbol,
        fetched_at=finished_at,
        latency_ms=None,
        message=classification,
    )
    insert_job_run(
        session,
        job_name=job_name,
        status="error",
        started_at=started_at,
        finished_at=finished_at,
        details={key: value for key, value in result.items() if key != "fetched_at"},
    )
    session.commit()
    return result


def _success_result(
    *,
    provider: str,
    symbol: str | None,
    raw_rows: int,
    valid_rows: int,
    saved_rows: int,
    rejected_rows: int,
    latency_ms: int,
    fetched_at: datetime,
) -> dict[str, Any]:
    if valid_rows == 0:
        status, classification = "skipped", "no_valid_rows"
    elif saved_rows == 0:
        status, classification = "success", "idempotent_replay"
    else:
        status, classification = "success", "new_rows_saved"
    return {
        "status": status,
        "classification": classification,
        "provider": provider,
        "symbol": symbol,
        "raw_rows": raw_rows,
        "valid_rows": valid_rows,
        "saved_rows": saved_rows,
        "existing_rows": max(valid_rows - saved_rows, 0),
        "rejected_rows": rejected_rows,
        "latency_ms": latency_ms,
        "fetched_at": fetched_at.isoformat(),
        "writes_database": saved_rows > 0,
    }


def collect_jquants_financial_summary(
    session: Session,
    code: str,
    from_date: str | None = None,
    to_date: str | None = None,
    client: JQuantsClient | None = None,
) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    symbol: str | None = None
    try:
        symbol = normalize_symbol(code)
        validate_date_range(from_date, to_date)
        asset = _asset(session, symbol)
        if asset.source != "jquants" or asset.asset_type not in {"stock", "etf"}:
            raise CollectionInputError("asset_source_mismatch")
        rows, latency_ms = (client or JQuantsClient()).fetch_financial_summary(
            symbol, from_date, to_date
        )
        normalized = normalize_financial_summary(rows.to_dict("records"))
        if not normalized.empty and any(
            normalize_symbol(value) != symbol for value in normalized["symbol"].tolist()
        ):
            raise CollectionInputError("provider_symbol_mismatch")
        fetched_at = datetime.now(UTC)
        payload, rejected = _fundamental_payload(
            asset, normalized, source="jquants", fetched_at=fetched_at
        )
        saved = insert_fundamental_snapshots(session, payload)
        result = _success_result(
            provider="jquants",
            symbol=symbol,
            raw_rows=len(rows),
            valid_rows=len(payload),
            saved_rows=saved,
            rejected_rows=max(len(rows) - len(normalized), 0) + rejected,
            latency_ms=latency_ms,
            fetched_at=fetched_at,
        )
        return _record_result(
            session,
            job_name="collect_jquants_financial_summary",
            provider="jquants",
            endpoint="/v2/fins/summary",
            symbol=symbol,
            started_at=started_at,
            result=result,
        )
    except Exception as exc:
        return _record_failure(
            session,
            job_name="collect_jquants_financial_summary",
            provider="jquants",
            endpoint="/v2/fins/summary",
            symbol=symbol,
            started_at=started_at,
            exc=exc,
        )


def collect_sec_fundamentals(
    session: Session,
    cik: str,
    symbol: str,
    client: SecClient | None = None,
) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    normalized_symbol: str | None = None
    try:
        normalized_symbol = normalize_symbol(symbol)
        normalized_cik = normalize_cik(cik)
        asset = _asset(session, normalized_symbol)
        if asset.asset_type not in {"stock", "etf"} or asset.currency != "USD":
            raise CollectionInputError("asset_source_mismatch")
        if asset.sec_cik != normalized_cik:
            raise CollectionInputError("asset_cik_mismatch")
        payload, latency_ms = (client or SecClient()).fetch_companyfacts(normalized_cik)
        normalized = normalize_sec_companyfacts(payload, symbol=normalized_symbol)
        fetched_at = datetime.now(UTC)
        rows, rejected = _fundamental_payload(
            asset, normalized, source="sec_companyfacts", fetched_at=fetched_at
        )
        saved = insert_fundamental_snapshots(session, rows)
        result = _success_result(
            provider="sec_companyfacts",
            symbol=normalized_symbol,
            raw_rows=len(normalized),
            valid_rows=len(rows),
            saved_rows=saved,
            rejected_rows=rejected,
            latency_ms=latency_ms,
            fetched_at=fetched_at,
        )
        result["cik"] = normalized_cik
        return _record_result(
            session,
            job_name="collect_sec_fundamentals",
            provider="sec_companyfacts",
            endpoint="/api/xbrl/companyfacts",
            symbol=normalized_symbol,
            started_at=started_at,
            result=result,
        )
    except Exception as exc:
        return _record_failure(
            session,
            job_name="collect_sec_fundamentals",
            provider="sec_companyfacts",
            endpoint="/api/xbrl/companyfacts",
            symbol=normalized_symbol,
            started_at=started_at,
            exc=exc,
        )


def save_reviewed_etf_metrics(
    session: Session,
    file_path: Path,
    source: str = "provider_reviewed",
) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    provider_for_log = "provider_reviewed"
    try:
        normalized_source = normalize_source(source)
        provider_for_log = normalized_source
        raw_payload = json.loads(file_path.read_text(encoding="utf-8"))
        if isinstance(raw_payload, list):
            raw_rows = raw_payload
        elif isinstance(raw_payload, dict):
            raw_rows = raw_payload.get("items", [])
        else:
            raise CollectionInputError("invalid_etf_payload")
        if not isinstance(raw_rows, list):
            raise CollectionInputError("invalid_etf_payload")
        normalized = normalize_etf_metrics(raw_rows)
        fetched_at = datetime.now(UTC)
        payload: list[dict[str, Any]] = []
        symbols: list[str] = []
        for row in normalized.to_dict("records"):
            symbol = normalize_symbol(row["symbol"])
            asset = _asset(session, symbol)
            if asset.asset_type != "etf":
                raise CollectionInputError("asset_type_mismatch")
            details = {
                key: _python_value(value)
                for key, value in row.items()
                if key not in {"symbol", "observed_at"}
            }
            payload.append(
                {
                    "asset_id": asset.id,
                    "observed_at": row["observed_at"],
                    "source": normalized_source,
                    "details": details,
                    "fetched_at": fetched_at,
                }
            )
            symbols.append(symbol)
        saved = insert_etf_metric_snapshots(session, payload)
        result = _success_result(
            provider=normalized_source,
            symbol=symbols[0] if len(set(symbols)) == 1 else None,
            raw_rows=len(raw_rows),
            valid_rows=len(payload),
            saved_rows=saved,
            rejected_rows=max(len(raw_rows) - len(normalized), 0),
            latency_ms=0,
            fetched_at=fetched_at,
        )
        result["symbols"] = sorted(set(symbols))
        return _record_result(
            session,
            job_name="save_etf_metrics",
            provider=normalized_source,
            endpoint="reviewed_json_file",
            symbol=result["symbol"],
            started_at=started_at,
            result=result,
        )
    except Exception as exc:
        return _record_failure(
            session,
            job_name="save_etf_metrics",
            provider=provider_for_log,
            endpoint="reviewed_json_file",
            symbol=None,
            started_at=started_at,
            exc=exc,
        )
