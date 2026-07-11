from datetime import UTC, datetime
import json

from app.core.logging import configure_logging
from app.database.session import SessionLocal
from app.services.market_service import collect_fred_market_data, record_job


def main() -> None:
    configure_logging()
    started_at = datetime.now(UTC)
    with SessionLocal() as session:
        result = collect_fred_market_data(session)
        status = "success" if any(item["status"] == "success" for item in result.values()) else "skipped"
        record_job(session, "collect_us_market", status, started_at, result)
    print("FRED collection result:")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
