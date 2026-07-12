from datetime import UTC, date, datetime, timedelta
import argparse
import json

from app.core.logging import configure_logging
from app.database.repositories import list_assets_by_source
from app.database.session import SessionLocal
from app.services.market_service import (
    collect_jquants_daily_batch,
    collect_jquants_listed_info,
    record_job,
)


def recent_candidate_dates(today: date, lag_days: int = 91, lookback_days: int = 5) -> list[str]:
    candidates = []
    cursor = today - timedelta(days=lag_days)
    for _ in range(lookback_days):
        while cursor.weekday() >= 5:
            cursor -= timedelta(days=1)
        candidates.append(cursor.strftime("%Y%m%d"))
        cursor -= timedelta(days=1)
    return candidates


def collect_recent_daily_batch(
    session,
    today: date,
    lag_days: int,
    lookback_days: int,
    limit: int,
    codes: list[str] | None,
    asset_types: list[str],
    bootstrap_master_limit: int,
) -> dict:
    """Collect a recent delayed price date, initializing a small asset master if needed."""
    bootstrap_result = None
    if codes is None:
        assets = list_assets_by_source(session, "jquants", asset_types=asset_types, limit=1)
        if not assets:
            bootstrap_result = collect_jquants_listed_info(session, limit=bootstrap_master_limit)
            if bootstrap_result["status"] != "success":
                return {
                    "status": bootstrap_result["status"],
                    "message": "J-Quants asset master bootstrap did not complete.",
                    "bootstrap": bootstrap_result,
                    "attempts": {},
                }

    attempts = {}
    final_result = {"status": "skipped", "message": "No candidate dates produced data", "attempts": attempts}
    for candidate_date in recent_candidate_dates(today, lag_days, lookback_days):
        result = collect_jquants_daily_batch(
            session,
            date=candidate_date,
            codes=codes,
            limit=limit,
            asset_types=asset_types,
        )
        attempts[candidate_date] = result
        if result.get("success", 0) > 0:
            final_result = {
                "status": result["status"],
                "selected_date": candidate_date,
                **result,
                "attempts": attempts,
            }
            break
    if bootstrap_result:
        final_result["bootstrap"] = bootstrap_result
    return final_result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect delayed daily bars for recent Free plan dates from J-Quants."
    )
    parser.add_argument("--lag-days", type=int, default=91, help="Days to lag from today to avoid the 12-week delay")
    parser.add_argument("--lookback-days", type=int, default=5, help="Number of candidate trading dates to try")
    parser.add_argument("--limit", type=int, default=3, help="Maximum number of assets to collect")
    parser.add_argument("--codes", help="Comma-separated J-Quants security codes")
    parser.add_argument("--asset-types", default="stock,etf", help="Comma-separated asset types when codes are omitted")
    parser.add_argument(
        "--bootstrap-master-limit",
        type=int,
        default=200,
        help="Assets to seed when the J-Quants master is empty",
    )
    args = parser.parse_args()

    codes = [item.strip() for item in args.codes.split(",") if item.strip()] if args.codes else None
    asset_types = [item.strip() for item in args.asset_types.split(",") if item.strip()]

    configure_logging()
    started_at = datetime.now(UTC)
    with SessionLocal() as session:
        final_result = collect_recent_daily_batch(
            session,
            today=date.today(),
            lag_days=args.lag_days,
            lookback_days=args.lookback_days,
            limit=args.limit,
            codes=codes,
            asset_types=asset_types,
            bootstrap_master_limit=args.bootstrap_master_limit,
        )
        record_job(session, "collect_jquants_recent_daily_batch", final_result["status"], started_at, final_result)

    print("J-Quants recent daily batch collection result:")
    print(json.dumps(final_result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
