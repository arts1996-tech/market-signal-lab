from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, timedelta
import json
from time import sleep

from app.core.logging import configure_logging
from app.analysis.market_calendar import exchange_calendar
from app.database.repositories import (
    collection_target_records,
    has_collected_price_for_date,
    list_assets_by_source,
    list_assets_missing_price_for_date,
    latest_successful_job_run,
    upsert_collection_target,
    upsert_unavailable_collection_items,
)
from app.database.session import SessionLocal
from app.services.market_service import (
    collect_jquants_daily_batch,
    collect_jquants_listed_info,
    record_job,
)


SOURCE = "jquants"
ASSET_TYPES = ["stock", "etf"]


def candidate_dates(latest_date: date, history_days: int) -> list[date]:
    """Return the latest JPX session first, then historical sessions oldest-first."""
    cutoff = latest_date - timedelta(days=history_days)
    sessions = exchange_calendar("XTKS").sessions_in_range(cutoff, latest_date)
    dates = [session.date() for session in sessions]
    return ([latest_date] if dates and dates[-1] == latest_date else []) + [value for value in dates if value < latest_date]


def should_probe_latest_target(
    target, now: datetime, probe_interval_hours: int
) -> bool:
    """Probe an unavailable latest target at startup and periodically thereafter."""
    if target is None or target.status != "unavailable":
        return True
    if target.checked_at is None:
        return True
    return target.checked_at <= now - timedelta(hours=probe_interval_hours)


def select_next_work(
    session,
    today: date,
    lag_days: int,
    history_days: int,
    limit: int,
    latest_probe_hours: int = 6,
):
    latest_date = today - timedelta(days=lag_days)
    dates = candidate_dates(latest_date, history_days)
    targets = collection_target_records(session, SOURCE, dates)
    for target_date in dates:
        target = targets.get(target_date)
        if target_date == latest_date and not should_probe_latest_target(
            target, datetime.now(UTC), latest_probe_hours
        ):
            # The latest API-availability check is deliberately rate-limited. While
            # waiting, older sessions may be backfilled without repeatedly probing
            # the same unavailable date.
            continue
        if target is not None and target.status == "unavailable":
            upsert_collection_target(
                session,
                SOURCE,
                target_date,
                "active",
                datetime.now(UTC),
                {"reason": "reopened_legacy_unavailable_target"},
            )
            session.commit()
        assets = list_assets_missing_price_for_date(session, SOURCE, target_date, ASSET_TYPES, limit)
        if assets:
            return target_date, assets
        upsert_collection_target(
            session,
            SOURCE,
            target_date,
            "complete",
            datetime.now(UTC),
            {"reason": "all_assets_have_price_or_terminal_unavailable_status"},
        )
        session.commit()
    return None, []


def should_mark_target_unavailable(
    success_count: int, no_data_count: int, requested_count: int, has_existing_prices: bool
) -> bool:
    return (
        requested_count >= 3
        and success_count == 0
        and no_data_count == requested_count
        and not has_existing_prices
    )


def collect_next_price_batch(
    session,
    today: date,
    lag_days: int,
    history_days: int,
    limit: int,
    master_refresh_days: int,
    latest_probe_hours: int = 6,
) -> dict:
    now = datetime.now(UTC)
    latest_master = latest_successful_job_run(session, "collect_jquants_listed_info")
    if (
        latest_master is None
        or latest_master.finished_at is None
        or latest_master.finished_at < now - timedelta(days=master_refresh_days)
    ):
        refresh_started_at = datetime.now(UTC)
        refresh = collect_jquants_listed_info(session)
        # The continuous collector must record the refresh itself. Without this
        # job-run entry every loop sees a stale/absent master and refreshes forever.
        record_job(session, "collect_jquants_listed_info", refresh["status"], refresh_started_at, refresh)
        return {
            "status": refresh["status"],
            "phase": "asset_master_refresh",
            "master_refresh": refresh,
            "message": "Full J-Quants asset master refresh completed; price collection resumes on the next run.",
        }

    assets = list_assets_by_source(session, SOURCE, asset_types=ASSET_TYPES, limit=1)
    if not assets:
        bootstrap = collect_jquants_listed_info(session)
        return {
            "status": bootstrap["status"],
            "phase": "asset_master_bootstrap",
            "bootstrap": bootstrap,
            "message": "Full asset master collection was requested; price collection starts on the next run.",
        }

    target_date, assets = select_next_work(
        session, today, lag_days, history_days, limit, latest_probe_hours
    )
    if target_date is None:
        return {"status": "success", "phase": "complete", "message": "No pending price collection work."}

    symbols = [asset.symbol for asset in assets]
    result = collect_jquants_daily_batch(
        session,
        date=target_date.strftime("%Y%m%d"),
        codes=symbols,
        limit=None,
        asset_types=ASSET_TYPES,
    )
    details = result.get("details", {})
    success_count = result.get("success", 0)
    no_data_count = result.get("no_data", 0)
    now = datetime.now(UTC)
    has_existing_prices = has_collected_price_for_date(session, SOURCE, target_date)
    if should_mark_target_unavailable(success_count, no_data_count, len(symbols), has_existing_prices):
        upsert_collection_target(
            session,
            SOURCE,
            target_date,
            "unavailable",
            now,
            {"reason": "multiple_assets_confirmed_no_data", "symbols": symbols},
        )
        unavailable_count = 0
    else:
        upsert_collection_target(session, SOURCE, target_date, "active", now, {"last_symbols": symbols})
        unavailable_count = upsert_unavailable_collection_items(
            session,
            SOURCE,
            target_date,
            {asset.symbol: asset for asset in assets},
            details,
            now,
        )
    session.commit()
    return {
        "status": result["status"],
        "phase": "price_collection",
        "target_date": target_date.isoformat(),
        "requested_symbols": symbols,
        "unavailable_marked": unavailable_count,
        **result,
    }


def sleep_seconds_for_result(result: dict) -> int:
    if result.get("phase") == "complete":
        return 3_600
    if result.get("status") in {"error", "retry_pending", "skipped"}:
        return 900
    return 15


def run_once(session, args, today: date | None = None) -> dict:
    started_at = datetime.now(UTC)
    result = collect_next_price_batch(
        session,
        today=today or date.today(),
        lag_days=args.lag_days,
        history_days=args.history_days,
        limit=args.limit,
        master_refresh_days=args.master_refresh_days,
        latest_probe_hours=args.latest_probe_hours,
    )
    record_job(session, "collect_jquants_all_prices", result["status"], started_at, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect all J-Quants Free plan daily prices incrementally.")
    parser.add_argument("--lag-days", type=int, default=91)
    parser.add_argument("--history-days", type=int, default=720)
    parser.add_argument("--limit", type=int, default=5, help="Maximum price requests for one run")
    parser.add_argument("--master-refresh-days", type=int, default=7)
    parser.add_argument(
        "--latest-probe-hours",
        type=int,
        default=6,
        help="Minimum interval before retrying an unavailable latest date",
    )
    parser.add_argument("--continuous", action="store_true", help="Run continuously with a safe request interval")
    args = parser.parse_args()

    configure_logging()
    if args.continuous and args.limit != 1:
        parser.error("--continuous requires --limit 1 to keep at least 15 seconds between requests")

    while True:
        with SessionLocal() as session:
            result = run_once(session, args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not args.continuous:
            return
        sleep(sleep_seconds_for_result(result))


if __name__ == "__main__":
    main()
