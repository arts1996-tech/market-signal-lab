from datetime import UTC, datetime
import argparse
import json

from app.core.logging import configure_logging
from app.database.session import SessionLocal
from app.services.market_service import collect_jquants_daily_batch, record_job


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect delayed daily bars for multiple J-Quants assets.")
    parser.add_argument("--date", required=True, help="Trading date in YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--codes", help="Comma-separated J-Quants security codes, e.g. 86970,72030")
    parser.add_argument("--limit", type=int, help="Maximum number of assets to collect")
    parser.add_argument("--asset-types", default="stock,etf", help="Comma-separated asset types when codes are omitted")
    args = parser.parse_args()

    codes = [item.strip() for item in args.codes.split(",") if item.strip()] if args.codes else None
    asset_types = [item.strip() for item in args.asset_types.split(",") if item.strip()]

    configure_logging()
    started_at = datetime.now(UTC)
    with SessionLocal() as session:
        result = collect_jquants_daily_batch(
            session,
            date=args.date,
            codes=codes,
            limit=args.limit,
            asset_types=asset_types,
        )
        record_job(session, "collect_jquants_daily_batch", result["status"], started_at, result)

    print("J-Quants daily batch collection result:")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
