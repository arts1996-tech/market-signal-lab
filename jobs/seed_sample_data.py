from datetime import UTC, datetime

from app.core.logging import configure_logging
from app.database.session import SessionLocal
from app.services.market_service import record_job, seed_sample_data


def main() -> None:
    configure_logging()
    started_at = datetime.now(UTC)
    with SessionLocal() as session:
        count = seed_sample_data(session)
        record_job(session, "seed_sample_data", "success", started_at, {"saved_rows": count})
    print(f"Seeded {count} sample market rows")


if __name__ == "__main__":
    main()

