from datetime import UTC, datetime
import logging

from sqlalchemy.orm import Session

from app.collectors.fred import FRED_INDEX_SERIES, FredClient
from app.collectors.sample_data import generate_sample_market_data
from app.core.exceptions import DataProviderError
from app.database.repositories import (
    ASSET_DEFINITIONS,
    insert_api_fetch_log,
    insert_job_run,
    upsert_assets,
    upsert_market_prices,
)

logger = logging.getLogger(__name__)


def concise_error_message(exc: Exception) -> str:
    message = str(exc).splitlines()[0]
    if "[SQL:" in message:
        message = message.split("[SQL:", 1)[0].strip()
    return f"{exc.__class__.__name__}: {message}"


def ensure_asset_master(session: Session) -> dict:
    assets = upsert_assets(session, ASSET_DEFINITIONS)
    session.commit()
    return assets


def save_price_frame(session: Session, frame) -> int:
    assets = ensure_asset_master(session)
    payload = []
    for row in frame.to_dict(orient="records"):
        asset = assets[row["symbol"]]
        payload.append(
            {
                "asset_id": asset.id,
                "timeframe": "1d",
                "price_time": row["price_time"],
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row["close"],
                "adjusted_close": row.get("adjusted_close"),
                "volume": row.get("volume"),
                "source": row["source"],
                "fetched_at": row["fetched_at"],
            }
        )
    count = upsert_market_prices(session, payload)
    session.commit()
    return count


def seed_sample_data(session: Session) -> int:
    frame = generate_sample_market_data()
    count = save_price_frame(session, frame)
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


def collect_fred_market_data(session: Session, observation_start: str | None = None) -> dict[str, dict]:
    ensure_asset_master(session)
    client = FredClient()
    result: dict[str, dict] = {}
    for symbol in FRED_INDEX_SERIES:
        try:
            frame, latency_ms = client.fetch_series(symbol, observation_start=observation_start)
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
