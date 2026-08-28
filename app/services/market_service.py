from datetime import UTC, date as date_value, datetime
import hashlib
import json
import logging

from sqlalchemy.orm import Session

from app.collectors.fred import FRED_INDEX_SERIES
from app.collectors.jquants import JQuantsClient
from app.collectors.sample_data import SAMPLE_ASSET_DEFINITIONS, generate_sample_market_data
from app.core.exceptions import DataProviderError
from app.core.security import redact_sensitive_text
from app.database.repositories import (
    ASSET_DEFINITIONS,
    insert_asset_lifecycle_records,
    insert_asset_universe_coverages,
    insert_api_fetch_log,
    insert_job_run,
    list_assets_by_source,
    upsert_assets,
    upsert_market_prices,
)
from app.providers.base import DataProvider
from app.providers.fred import FredMarketProvider

logger = logging.getLogger(__name__)


def concise_error_message(exc: Exception) -> str:
    message = str(exc).splitlines()[0]
    if "[SQL:" in message:
        message = message.split("[SQL:", 1)[0].strip()
    return redact_sensitive_text(f"{exc.__class__.__name__}: {message}")


def ensure_asset_master(session: Session, definitions: list[dict] | None = None) -> dict:
    assets = upsert_assets(session, definitions or ASSET_DEFINITIONS)
    session.commit()
    return assets


def save_price_frame(session: Session, frame, asset_definitions: list[dict] | None = None) -> int:
    assets = ensure_asset_master(session, asset_definitions)
    payload = []
    for row in frame.to_dict(orient="records"):
        asset = assets[row["symbol"]]
        payload.append(
            {
                "asset_id": asset.id,
                "timeframe": "1d",
                "price_time": row["price_time"],
                "session_date": row.get("session_date") or row["price_time"].date(),
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row["close"],
                "adjusted_close": row.get("adjusted_close"),
                "adjusted_open": row.get("adjusted_open"),
                "adjusted_high": row.get("adjusted_high"),
                "adjusted_low": row.get("adjusted_low"),
                "adjusted_volume": row.get("adjusted_volume"),
                "adjustment_factor": row.get("adjustment_factor"),
                "volume": row.get("volume"),
                "source": row["source"],
                "source_symbol": row.get("source_symbol") or row["symbol"],
                "fetched_at": row["fetched_at"],
                "available_at": row.get("available_at") or row["fetched_at"],
                "data_quality_status": row.get("data_quality_status", "unknown"),
                "price_basis": row.get("price_basis", "legacy_unknown"),
            }
        )
    count = upsert_market_prices(session, payload)
    session.commit()
    return count


def seed_sample_data(session: Session) -> int:
    frame = generate_sample_market_data()
    count = save_price_frame(session, frame, ASSET_DEFINITIONS + SAMPLE_ASSET_DEFINITIONS)
    insert_api_fetch_log(
        session,
        provider="sample",
        endpoint="generate_sample_market_data",
        status="success",
        asset_symbol=None,
        fetched_at=datetime.now(UTC),
        latency_ms=0,
        message=f"Seeded {count} sample observations",
    )
    session.commit()
    return count


def collect_fred_market_data(
    session: Session,
    observation_start: str | None = None,
    provider: DataProvider | None = None,
) -> dict[str, dict]:
    provider = provider or FredMarketProvider()
    upsert_assets(session, provider.fetch_assets())
    session.commit()
    result: dict[str, dict] = {}
    for symbol in FRED_INDEX_SERIES:
        try:
            frame, latency_ms = provider.fetch_prices(symbol, observation_start=observation_start)
            saved_rows = save_price_frame(session, frame)
            result[symbol] = {"status": "success", "saved_rows": saved_rows, "latency_ms": latency_ms}
            insert_api_fetch_log(
                session,
                provider="fred",
                endpoint="/series/observations",
                status="success",
                asset_symbol=symbol,
                fetched_at=datetime.now(UTC),
                latency_ms=latency_ms,
                message=f"Saved {saved_rows} observations",
            )
        except DataProviderError as exc:
            logger.warning("FRED collection skipped for %s: %s", symbol, exc)
            message = concise_error_message(exc)
            result[symbol] = {"status": "skipped", "message": message}
            insert_api_fetch_log(
                session,
                provider="fred",
                endpoint="/series/observations",
                status="skipped",
                asset_symbol=symbol,
                fetched_at=datetime.now(UTC),
                latency_ms=None,
                message=message,
            )
        except Exception as exc:
            session.rollback()
            message = concise_error_message(exc)
            logger.exception("FRED collection failed for %s: %s", symbol, message)
            result[symbol] = {"status": "error", "message": message}
            insert_api_fetch_log(
                session,
                provider="fred",
                endpoint="/series/observations",
                status="error",
                asset_symbol=symbol,
                fetched_at=datetime.now(UTC),
                latency_ms=None,
                message=message,
            )
    session.commit()
    return result


def collect_jquants_daily_bars(
    session: Session,
    code: str,
    date: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    name: str | None = None,
    asset_type: str = "stock",
) -> dict:
    assets = upsert_assets(
        session,
        [
            {
                "symbol": code,
                "name": name or f"J-Quants {code}",
                "asset_type": asset_type,
                "currency": "JPY",
                "exchange": "JPX",
                "source": "jquants",
                "metadata_json": {"free_plan_note": "J-Quants Free plan data is delayed by 12 weeks."},
            }
        ],
    )
    session.commit()
    client = JQuantsClient()
    try:
        frame, latency_ms = client.fetch_daily_bars(code, date=date, from_date=from_date, to_date=to_date)
        if frame.empty:
            message = (
                "J-Quants returned no delayed Free plan observations. "
                "Try a past trading date outside the 12-week delay window."
            )
            insert_api_fetch_log(
                session,
                provider="jquants",
                endpoint="/v2/equities/bars/daily",
                status="skipped",
                asset_symbol=code,
                fetched_at=datetime.now(UTC),
                latency_ms=latency_ms,
                message=message,
            )
            session.commit()
            return {"status": "no_data", "saved_rows": 0, "latency_ms": latency_ms, "message": message}
        asset = assets[code]
        payload = []
        for row in frame.to_dict(orient="records"):
            payload.append(
                {
                    "asset_id": asset.id,
                    "timeframe": "1d",
                    "price_time": row["price_time"],
                    "session_date": row.get("session_date") or row["price_time"].date(),
                    "open": row.get("open"),
                    "high": row.get("high"),
                    "low": row.get("low"),
                    "close": row["close"],
                    "adjusted_close": row.get("adjusted_close"),
                    "adjusted_open": row.get("adjusted_open"),
                    "adjusted_high": row.get("adjusted_high"),
                    "adjusted_low": row.get("adjusted_low"),
                    "adjusted_volume": row.get("adjusted_volume"),
                    "adjustment_factor": row.get("adjustment_factor"),
                    "volume": row.get("volume"),
                    "source": row["source"],
                    "source_symbol": row.get("source_symbol") or code,
                    "fetched_at": row["fetched_at"],
                    "available_at": row.get("available_at") or row["fetched_at"],
                    "data_quality_status": row.get("data_quality_status", "complete_ohlcv"),
                    "price_basis": row.get("price_basis", "legacy_unknown"),
                }
            )
        saved_rows = upsert_market_prices(session, payload)
        insert_api_fetch_log(
            session,
            provider="jquants",
            endpoint="/v2/equities/bars/daily",
            status="success",
            asset_symbol=code,
            fetched_at=datetime.now(UTC),
            latency_ms=latency_ms,
            message=f"Saved {saved_rows} delayed Free plan observations",
        )
        session.commit()
        return {"status": "success", "saved_rows": saved_rows, "latency_ms": latency_ms}
    except DataProviderError as exc:
        message = concise_error_message(exc)
        status = "retry_pending" if exc.retryable else "error"
        insert_api_fetch_log(
            session,
            provider="jquants",
            endpoint="/v2/equities/bars/daily",
            status=status,
            asset_symbol=code,
            fetched_at=datetime.now(UTC),
            latency_ms=None,
            message=message,
        )
        session.commit()
        return {"status": status, "message": message, "error_category": exc.category}
    except Exception as exc:
        session.rollback()
        message = concise_error_message(exc)
        logger.exception("J-Quants collection failed for %s: %s", code, message)
        insert_api_fetch_log(
            session,
            provider="jquants",
            endpoint="/v2/equities/bars/daily",
            status="error",
            asset_symbol=code,
            fetched_at=datetime.now(UTC),
            latency_ms=None,
            message=message,
        )
        session.commit()
        return {"status": "error", "message": message}


def collect_jquants_listed_info(session: Session, date: str | None = None, limit: int | None = None) -> dict:
    client = JQuantsClient()
    try:
        assets, latency_ms, endpoint = client.fetch_listed_info(date=date)
        selected_assets = assets[:limit] if limit else assets
        asset_rows = upsert_assets(session, selected_assets)
        fetched_at = datetime.now(UTC)
        lifecycle_rows = []
        snapshot_dates: set[date_value] = set()
        for item in selected_assets:
            lifecycle = item.get("metadata_json", {}).get("lifecycle", {})
            raw_effective = lifecycle.get("effective_date") or date
            if not raw_effective:
                continue
            effective = datetime.strptime(str(raw_effective).replace("-", "")[:8], "%Y%m%d").date()
            snapshot_dates.add(effective)
            available_at = datetime.combine(effective, datetime.min.time(), tzinfo=UTC)

            def optional_date(value):
                if not value:
                    return None
                return datetime.strptime(str(value).replace("-", "")[:8], "%Y%m%d").date()

            metadata = item.get("metadata_json", {})
            lifecycle_rows.append(
                {
                    "asset_id": asset_rows[item["symbol"]].id,
                    "effective_from": effective,
                    "effective_to": effective,
                    "listed_on": optional_date(lifecycle.get("listed_on")),
                    "delisted_on": optional_date(lifecycle.get("delisted_on")),
                    "market": metadata.get("market"),
                    "sector_17": metadata.get("sector_17"),
                    "sector_33": metadata.get("sector_33"),
                    "investability_status": "investable",
                    "source": "jquants_listed_info",
                    "available_at": available_at,
                    "fetched_at": fetched_at,
                    "details": {"provider_fields_only": True},
                }
            )
        saved_lifecycle = insert_asset_lifecycle_records(session, lifecycle_rows)
        saved_coverage = 0
        coverage_status = "unverified_missing_effective_date"
        if len(snapshot_dates) == 1 and len(lifecycle_rows) == len(selected_assets):
            snapshot_date = next(iter(snapshot_dates))
            coverage_status = "partial" if limit is not None else "complete"
            digest = hashlib.sha256(
                json.dumps(sorted(item["symbol"] for item in selected_assets)).encode()
            ).hexdigest()
            saved_coverage = insert_asset_universe_coverages(
                session,
                [{
                    "period_start": snapshot_date,
                    "period_end": snapshot_date,
                    "status": coverage_status,
                    "source": "jquants_listed_info",
                    "observed_asset_count": len(selected_assets),
                    "input_hash": digest,
                    "available_at": datetime.combine(snapshot_date, datetime.min.time(), tzinfo=UTC),
                    "checked_at": fetched_at,
                    "details": {"requested_date": date, "limited": limit is not None},
                }],
            )
        insert_api_fetch_log(
            session,
            provider="jquants",
            endpoint=endpoint,
            status="success",
            asset_symbol=None,
            fetched_at=fetched_at,
            latency_ms=latency_ms,
            message=(
                f"Saved {len(selected_assets)} listed assets; "
                f"lifecycle={saved_lifecycle}; universe={coverage_status}"
            ),
        )
        session.commit()
        return {
            "status": "success",
            "saved_assets": len(selected_assets),
            "saved_lifecycle_records": saved_lifecycle,
            "saved_universe_coverages": saved_coverage,
            "universe_coverage_status": coverage_status,
            "latency_ms": latency_ms,
        }
    except DataProviderError as exc:
        message = concise_error_message(exc)
        insert_api_fetch_log(
            session,
            provider="jquants",
            endpoint="/v2/listed/info",
            status="skipped",
            asset_symbol=None,
            fetched_at=datetime.now(UTC),
            latency_ms=None,
            message=message,
        )
        session.commit()
        return {"status": "skipped", "message": message}
    except Exception as exc:
        session.rollback()
        message = concise_error_message(exc)
        logger.exception("J-Quants listed info collection failed: %s", message)
        insert_api_fetch_log(
            session,
            provider="jquants",
            endpoint="/v2/listed/info",
            status="error",
            asset_symbol=None,
            fetched_at=datetime.now(UTC),
            latency_ms=None,
            message=message,
        )
        session.commit()
        return {"status": "error", "message": message}


def collect_jquants_daily_batch(
    session: Session,
    date: str,
    codes: list[str] | None = None,
    limit: int | None = None,
    asset_types: list[str] | None = None,
) -> dict:
    target_names: dict[str, str] = {}
    if codes:
        target_codes = codes[:limit] if limit else codes
    else:
        assets = list_assets_by_source(session, "jquants", asset_types=asset_types or ["stock", "etf"], limit=limit)
        target_codes = [asset.symbol for asset in assets]
        target_names = {asset.symbol: asset.name for asset in assets}

    client = JQuantsClient()
    result: dict[str, dict] = {}
    for index, code in enumerate(target_codes):
        try:
            item_result = collect_jquants_daily_bars(session, code=code, date=date, name=target_names.get(code))
            result[code] = item_result
        finally:
            if index < len(target_codes) - 1:
                client.respect_free_plan_rate_limit()
    success_count = sum(1 for item in result.values() if item.get("status") == "success")
    no_data_count = sum(1 for item in result.values() if item.get("status") == "no_data")
    retry_pending_count = sum(1 for item in result.values() if item.get("status") == "retry_pending")
    error_count = sum(1 for item in result.values() if item.get("status") == "error")
    if not target_codes:
        overall_status = "skipped"
    elif retry_pending_count and not success_count:
        overall_status = "retry_pending"
    elif error_count and not success_count:
        overall_status = "error"
    elif error_count or retry_pending_count or no_data_count:
        overall_status = "partial"
    else:
        overall_status = "success"
    return {
        "status": overall_status,
        "requested": len(target_codes),
        "success": success_count,
        "no_data": no_data_count,
        "retry_pending": retry_pending_count,
        "error": error_count,
        "details": result,
    }


def record_job(session: Session, name: str, status: str, started_at: datetime, details: dict) -> None:
    insert_job_run(
        session,
        job_name=name,
        status=status,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        details=details,
    )
    session.commit()
