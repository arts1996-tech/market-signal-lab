from datetime import UTC, datetime

from app.core.logging import configure_logging
from app.database.session import SessionLocal
from app.services.market_service import record_job


def main() -> None:
    configure_logging()
    started_at = datetime.now(UTC)
    with SessionLocal() as session:
        details = {"status": "placeholder", "note": "Backtest engine module is ready for signal rules."}
        record_job(session, "run_backtest", "success", started_at, details)
    print(f"Backtest summary: {details}")


if __name__ == "__main__":
    main()

