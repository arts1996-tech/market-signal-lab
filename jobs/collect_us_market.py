from datetime import UTC, datetime

from app.core.logging import configure_logging
from app.database.session import SessionLocal
from app.services.market_service import collect_fred_market_data, record_job


def main() -> None:
    configure_logging()
    started_at = datetime.now(UTC)
    with SessionLocal() as session:
        result = collect_fred_market_data(session)
        status = "success" if result else "skipped"
        record_job(session, "collect_us_market", status, started_at, result)
    print(f"FRED collection result: {result}")


if __name__ == "__main__":
    main()

