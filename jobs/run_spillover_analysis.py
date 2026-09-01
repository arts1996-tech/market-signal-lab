"""Persist observed US previous-session to Japan current-session features."""

import argparse
from datetime import UTC, datetime

from app.core.logging import configure_logging
from app.core.job_lock import HEAVY_ANALYSIS_LOCK, prevent_concurrent_runs
from app.database.session import SessionLocal
from app.services.analysis_service import persist_us_japan_spillover_features
from app.services.market_service import record_job


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jp-symbol", required=True, help="J-Quants stock or ETF symbol")
    parser.add_argument("--us-symbol", default="NASDAQCOM", help="FRED US index symbol")
    return parser.parse_args()


@prevent_concurrent_runs(HEAVY_ANALYSIS_LOCK)
def main() -> None:
    args = parse_args()
    configure_logging()
    started_at = datetime.now(UTC)
    with SessionLocal() as session:
        saved, analysis = persist_us_japan_spillover_features(session, args.us_symbol, args.jp_symbol)
        details = {
            "base_symbol": args.us_symbol,
            "target_symbol": args.jp_symbol,
            "matched_sessions": len(analysis["frame"]),
            "saved_features": saved,
            "saved_model_results": analysis["saved_model_results"],
            "warnings": analysis["warnings"],
        }
        record_job(session, "run_spillover_analysis", "success", started_at, details)
    print(f"US-Japan spillover analysis summary: {details}")


if __name__ == "__main__":
    main()
