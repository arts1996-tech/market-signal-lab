from datetime import UTC, datetime

from app.core.logging import configure_logging
from app.database.session import SessionLocal
from app.services.analysis_service import load_market_analysis, persist_us_japan_correlation_results
from app.services.market_service import record_job


def main() -> None:
    configure_logging()
    started_at = datetime.now(UTC)
    with SessionLocal() as session:
        analysis = load_market_analysis(session)
        saved_correlations = persist_us_japan_correlation_results(session)
        details = {
            "pairs": len(analysis["pair"]),
            "rolling_points": len(analysis["rolling_correlation"]),
            "saved_correlation_results": saved_correlations,
        }
        record_job(session, "run_short_term_analysis", "success", started_at, details)
    print(f"Short-term analysis summary: {details}")


if __name__ == "__main__":
    main()
