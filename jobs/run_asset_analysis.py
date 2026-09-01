"""Run versioned phase-3 analysis for every quality-gated asset."""

from datetime import UTC, datetime
import json

from app.backtest.audit import json_value
from app.core.logging import configure_logging
from app.core.job_lock import HEAVY_ANALYSIS_LOCK, prevent_concurrent_runs
from app.database.session import SessionLocal
from app.services.asset_analysis_service import run_all_asset_analysis
from app.services.market_service import record_job


@prevent_concurrent_runs(HEAVY_ANALYSIS_LOCK)
def main() -> None:
    configure_logging()
    started_at = datetime.now(UTC)
    try:
        with SessionLocal() as session:
            details = run_all_asset_analysis(session, started_at=started_at)
            record_job(
                session,
                "run_asset_analysis",
                details["status"],
                started_at,
                json_value(details),
            )
    except Exception as exc:
        try:
            with SessionLocal() as session:
                record_job(
                    session,
                    "run_asset_analysis",
                    "error",
                    started_at,
                    {"error_type": type(exc).__name__},
                )
        except Exception:
            pass
        raise
    print(json.dumps(json_value(details), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
