from datetime import UTC, datetime
import argparse

from app.core.logging import configure_logging
from app.core.config import get_settings
from app.database.session import SessionLocal
from app.services.market_service import record_job, seed_sample_data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed demo-only synthetic market data.")
    parser.add_argument("--demo", action="store_true", help="Required acknowledgement for synthetic data")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.demo or get_settings().market_data_mode != "demo":
        raise SystemExit("Sample data is demo-only. Set MARKET_DATA_MODE=demo and pass --demo.")
    configure_logging()
    started_at = datetime.now(UTC)
    with SessionLocal() as session:
        count = seed_sample_data(session)
        record_job(session, "seed_sample_data", "success", started_at, {"saved_rows": count})
    print(f"Seeded {count} sample market rows")


if __name__ == "__main__":
    main()
