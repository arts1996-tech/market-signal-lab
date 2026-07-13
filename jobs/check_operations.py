"""Read-only resource and database health snapshot for Raspberry Pi operations."""

import json
import shutil
from datetime import UTC, datetime

from sqlalchemy import text

from app.database.session import SessionLocal
from app.services.market_service import record_job


def main() -> None:
    usage = shutil.disk_usage("/")
    started_at = datetime.now(UTC)
    with SessionLocal() as session:
        prices = session.execute(text("SELECT count(*) FROM market_prices")).scalar_one()
        latest = session.execute(text("SELECT max(fetched_at) FROM market_prices")).scalar_one()
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
        "latest_fetched_at": latest.isoformat() if latest else None,
        "failed_or_retry_jobs_24h": failed_jobs,
        "recent_failures": recent_failures,
    }
    with SessionLocal() as session:
        record_job(session, "check_operations", status, started_at, details)
    print(json.dumps(details, ensure_ascii=False, default=str, indent=2))
    if status == "warning":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
