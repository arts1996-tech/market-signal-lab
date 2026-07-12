"""Read-only resource and database health snapshot for Raspberry Pi operations."""

import json
import shutil
from datetime import UTC, datetime

from sqlalchemy import text

from app.database.session import SessionLocal


def main() -> None:
    usage = shutil.disk_usage("/")
    with SessionLocal() as session:
        prices = session.execute(text("SELECT count(*) FROM market_prices")).scalar_one()
        latest = session.execute(text("SELECT max(fetched_at) FROM market_prices")).scalar_one()
        failed_jobs = session.execute(text("SELECT count(*) FROM job_runs WHERE status IN ('error', 'retry_pending') AND started_at >= now() - interval '24 hours'" )).scalar_one()
    print(json.dumps({
        "checked_at": datetime.now(UTC).isoformat(),
        "disk_total_bytes": usage.total,
        "disk_used_bytes": usage.used,
        "disk_free_bytes": usage.free,
        "disk_used_ratio": usage.used / usage.total,
        "market_prices": prices,
        "latest_fetched_at": latest.isoformat() if latest else None,
        "failed_or_retry_jobs_24h": failed_jobs,
    }, ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
