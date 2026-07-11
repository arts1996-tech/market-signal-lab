from datetime import UTC, datetime

from app.core.logging import configure_logging
from app.database.session import SessionLocal
from app.services.analysis_service import load_market_analysis
from app.services.market_service import record_job


def main() -> None:
    configure_logging()
    started_at = datetime.now(UTC)
    with SessionLocal() as session:
        analysis = load_market_analysis(session)
        details = {"pairs": len(analysis["pair"]), "rolling_points": len(analysis["rolling_correlation"])}
        record_job(session, "run_short_term_analysis", "success", started_at, details)
    print(f"Short-term analysis summary: {details}")


if __name__ == "__main__":
    main()

