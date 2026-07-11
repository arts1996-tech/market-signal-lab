from datetime import UTC, datetime
import argparse
import json

from app.core.logging import configure_logging
from app.database.session import SessionLocal
from app.services.market_service import collect_jquants_daily_bars, record_job


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect delayed daily bars from J-Quants Free plan.")
    parser.add_argument("--code", required=True, help="J-Quants security code, e.g. 86970")
    parser.add_argument("--from-date", dest="from_date", help="Start date in YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--to-date", dest="to_date", help="End date in YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--name", help="Asset display name")
    parser.add_argument("--asset-type", default="stock", choices=["stock", "etf"])
    args = parser.parse_args()

    configure_logging()
    started_at = datetime.now(UTC)
    with SessionLocal() as session:
        result = collect_jquants_daily_bars(
            session,
            code=args.code,
            from_date=args.from_date,
            to_date=args.to_date,
            name=args.name,
            asset_type=args.asset_type,
        )
        record_job(session, "collect_jquants_daily", result["status"], started_at, {"code": args.code, **result})

    print("J-Quants daily collection result:")
    print(json.dumps({args.code: result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
