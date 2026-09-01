from datetime import UTC, datetime

from app.core.logging import configure_logging
from app.core.job_lock import HEAVY_ANALYSIS_LOCK, prevent_concurrent_runs
from app.database.session import SessionLocal
from app.services.market_service import record_job
from app.services.mid_term_service import run_mid_term_analysis


@prevent_concurrent_runs(HEAVY_ANALYSIS_LOCK)
def main() -> None:
    configure_logging()
    started_at = datetime.now(UTC)
    try:
        with SessionLocal() as session:
            details = run_mid_term_analysis(session)
            record_job(session, "run_mid_term_analysis", details["status"], started_at, details)
    except Exception as exc:
        try:
            with SessionLocal() as session:
                record_job(
                    session,
                    "run_mid_term_analysis",
                    "error",
                    started_at,
                    {"error_type": type(exc).__name__},
                )
        except Exception:
            pass
        raise
    print(f"Mid-term analysis summary: {details}")


if __name__ == "__main__":
    main()
