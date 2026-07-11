from datetime import UTC, datetime
import argparse
import json

from app.core.logging import configure_logging
from app.database.session import SessionLocal
from app.services.market_service import collect_jquants_listed_info, record_job


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect listed asset info from J-Quants Free plan.")
    parser.add_argument("--date", help="Reference date in YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--limit", type=int, help="Save only the first N assets for a small trial run")
    args = parser.parse_args()

    configure_logging()
    started_at = datetime.now(UTC)
    with SessionLocal() as session:
        result = collect_jquants_listed_info(session, date=args.date, limit=args.limit)
        record_job(session, "collect_jquants_listed_info", result["status"], started_at, result)

    print("J-Quants listed info collection result:")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
