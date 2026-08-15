"""Read-only resource and database health snapshot for Raspberry Pi operations."""

import json
import os
import shutil
from datetime import UTC, datetime

from sqlalchemy import text

from app.analysis.market_calendar import latest_contiguous_exchange_observations
from app.database.session import SessionLocal
from app.services.market_service import record_job


def memory_snapshot() -> dict:
    values = {}
    try:
        for line in open("/proc/meminfo", encoding="utf-8"):
            key, value = line.split(":", 1)
            values[key] = int(value.strip().split()[0]) * 1024
    except (FileNotFoundError, OSError, ValueError):
        return {}
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", values.get("MemFree", 0))
    return {
        "memory_total_bytes": total,
        "memory_available_bytes": available,
        "memory_used_ratio": (total - available) / total if total else None,
    }


def contiguous_history_counts(rows, thresholds: tuple[int, ...] = (1, 5, 10, 20, 30)) -> dict:
    """Summarize latest contiguous XTKS sessions by asset."""
    dates_by_asset: dict[str, list] = {}
    for row in rows:
        asset_id = row["asset_id"]
        dates_by_asset.setdefault(asset_id, []).append(row["session_date"])
    counts = [
        latest_contiguous_exchange_observations(dates, "XTKS")
        for dates in dates_by_asset.values()
    ]
    return {
        **{f"ge_{threshold}": sum(count >= threshold for count in counts) for threshold in thresholds},
        "max_observations": max(counts, default=0),
    }


def main() -> None:
    usage = shutil.disk_usage("/")
    started_at = datetime.now(UTC)
    with SessionLocal() as session:
        prices = session.execute(text("SELECT count(*) FROM market_prices")).scalar_one()
        latest = session.execute(text("SELECT max(fetched_at) FROM market_prices")).scalar_one()
        observed_history_counts = session.execute(text("""
            SELECT
                count(*) FILTER (WHERE observations >= 1) AS ge_1,
                count(*) FILTER (WHERE observations >= 5) AS ge_5,
                count(*) FILTER (WHERE observations >= 10) AS ge_10,
                count(*) FILTER (WHERE observations >= 20) AS ge_20,
                count(*) FILTER (WHERE observations >= 30) AS ge_30,
                coalesce(max(observations), 0) AS max_observations
            FROM (
                SELECT asset_id, count(*) AS observations
                FROM market_prices
                WHERE source = 'jquants' AND price_basis = 'raw_ohlcv_with_adjusted'
                GROUP BY asset_id
            ) counts
        """)).mappings().one()
        contiguous_rows = session.execute(text("""
            SELECT asset_id, session_date
            FROM market_prices
            WHERE source = 'jquants'
              AND price_basis = 'raw_ohlcv_with_adjusted'
              AND adjusted_close IS NOT NULL
            ORDER BY asset_id, session_date
        """)).mappings().all()
        history_counts = contiguous_history_counts(contiguous_rows)
        target_counts = session.execute(text("""
            SELECT
                count(*) AS total,
                count(*) FILTER (WHERE status = 'complete') AS completed,
                min(session_date) FILTER (WHERE status IN ('active', 'pending')) AS next_session_date
            FROM price_collection_targets
            WHERE source = 'jquants'
        """)).mappings().one()
        item_counts = session.execute(text("""
            SELECT
                count(*) FILTER (WHERE status IN ('success', 'no_data')) AS completed,
                count(*) FILTER (WHERE status = 'retry_pending') AS retry_pending,
                count(*) FILTER (WHERE status = 'error') AS errors
            FROM price_collection_items
            WHERE source = 'jquants'
        """)).mappings().one()
        basis_counts = dict(session.execute(text("""
            SELECT coalesce(price_basis, 'NULL') AS basis, count(*) AS count
            FROM market_prices GROUP BY price_basis
        """)).all())
        phase3_counts = session.execute(text("""
            SELECT
                (SELECT count(*) FROM fundamental_snapshots) AS fundamental_snapshots,
                (SELECT count(*) FROM etf_metric_snapshots) AS etf_metric_snapshots,
                (SELECT count(*) FROM assets WHERE sec_cik IS NOT NULL) AS assets_with_sec_cik
        """)).mappings().one()
        failed_jobs = session.execute(text("SELECT count(*) FROM job_runs WHERE status IN ('error', 'retry_pending') AND started_at >= now() - interval '24 hours'" )).scalar_one()
        recent_failures = [
            {
                **dict(row),
                "started_at": row["started_at"].isoformat() if row["started_at"] else None,
            }
            for row in session.execute(text("""
                SELECT job_name, status, started_at, details
                FROM job_runs
                WHERE status IN ('error', 'retry_pending')
                ORDER BY started_at DESC LIMIT 20
            """)).mappings().all()
        ]
    status = "ok"
    warnings = []
    if usage.used / usage.total >= 0.85:
        status = "warning"
        warnings.append("disk_usage_over_85_percent")
    if failed_jobs >= 100:
        status = "warning"
        warnings.append("too_many_failed_or_retry_jobs")
    details = {
        "status": status,
        "warnings": warnings,
        "checked_at": datetime.now(UTC).isoformat(),
        "disk_total_bytes": usage.total,
        "disk_used_bytes": usage.used,
        "disk_free_bytes": usage.free,
        "disk_used_ratio": usage.used / usage.total,
        "market_prices": prices,
        "adjusted_history_ready_symbols": history_counts["ge_30"],
        "adjusted_history_symbols_by_threshold": {
            "1": history_counts["ge_1"],
            "5": history_counts["ge_5"],
            "10": history_counts["ge_10"],
            "20": history_counts["ge_20"],
            "30": history_counts["ge_30"],
        },
        "adjusted_history_max_observations": history_counts["max_observations"],
        "adjusted_observed_history_symbols_by_threshold": {
            "1": observed_history_counts["ge_1"],
            "5": observed_history_counts["ge_5"],
            "10": observed_history_counts["ge_10"],
            "20": observed_history_counts["ge_20"],
            "30": observed_history_counts["ge_30"],
        },
        "adjusted_observed_history_max_observations": observed_history_counts["max_observations"],
        "collection_targets_total": target_counts["total"],
        "collection_targets_completed": target_counts["completed"],
        "collection_targets_progress_ratio": (
            target_counts["completed"] / target_counts["total"]
            if target_counts["total"] else None
        ),
        "collection_next_session_date": str(target_counts["next_session_date"])
        if target_counts["next_session_date"] else None,
        "collection_items_completed": item_counts["completed"],
        "collection_items_retry_pending": item_counts["retry_pending"],
        "collection_items_errors": item_counts["errors"],
        "price_basis_counts": basis_counts,
        "fundamental_snapshots": phase3_counts["fundamental_snapshots"],
        "etf_metric_snapshots": phase3_counts["etf_metric_snapshots"],
        "assets_with_sec_cik": phase3_counts["assets_with_sec_cik"],
        "latest_fetched_at": latest.isoformat() if latest else None,
        "failed_or_retry_jobs_24h": failed_jobs,
        "recent_failures": recent_failures,
        "cpu_count": os.cpu_count(),
        "load_average_1m": os.getloadavg()[0] if hasattr(os, "getloadavg") else None,
        **memory_snapshot(),
    }
    with SessionLocal() as session:
        record_job(session, "check_operations", status, started_at, details)
    print(json.dumps(details, ensure_ascii=False, default=str, indent=2))
    if status == "warning":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
