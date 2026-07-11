from datetime import UTC, datetime

from app.core.logging import configure_logging
from app.database.session import SessionLocal
from app.services.market_service import record_job


def main() -> None:
    configure_logging()
    started_at = datetime.now(UTC)
    with SessionLocal() as session:
        details = {"status": "placeholder", "note": "Fundamental data providers are not enabled yet."}
        record_job(session, "run_mid_term_analysis", "success", started_at, details)
    print(f"Mid-term analysis summary: {details}")


if __name__ == "__main__":
    main()

