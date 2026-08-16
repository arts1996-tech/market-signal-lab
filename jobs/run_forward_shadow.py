from datetime import UTC, datetime
import argparse
from pathlib import Path

import pandas as pd

from app.analysis.demo_portfolio import run_demo_portfolio_environment
from app.analysis.market_calendar import is_exchange_session
from app.backtest.shadow import write_forward_shadow_snapshot
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.database.session import SessionLocal
from app.services.analysis_service import load_movement_and_virtual_trade_analysis


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record immutable virtual-account observations without placing orders."
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Use only deterministic synthetic demo inputs.",
    )
    parser.add_argument(
        "--output-dir",
        default="data/forward_shadow",
        help="Git-ignored directory for JSON observations.",
    )
    parser.add_argument("--score-threshold", type=int, default=70)
    parser.add_argument("--holding-days", type=int, default=5)
    parser.add_argument(
        "--daily",
        action="store_true",
        help="Freeze at most one snapshot per account and Japan calendar day.",
    )
    parser.add_argument(
        "--observed-at",
        help="Optional ISO-8601 observation time for controlled replay and testing.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging()
    observed_at = pd.Timestamp(args.observed_at or datetime.now(UTC))
    if observed_at.tzinfo is None:
        observed_at = observed_at.tz_localize("UTC")
    else:
        observed_at = observed_at.tz_convert("UTC")
    observation_date_jst = observed_at.tz_convert("Asia/Tokyo").normalize()
    if args.daily and not is_exchange_session(observation_date_jst, "XTKS"):
        print(
            f"Forward-shadow skipped: {observation_date_jst.date()} is not a Tokyo Stock Exchange session."
        )
        return
    settings = get_settings()
    if args.demo:
        if settings.market_data_mode != "demo":
            raise SystemExit("Synthetic forward recording requires MARKET_DATA_MODE=demo.")
        environment = run_demo_portfolio_environment()
        accounts = environment["accounts"]
    else:
        if settings.market_data_mode == "demo":
            raise SystemExit("Real-data forward recording is disabled in demo mode.")
        with SessionLocal() as session:
            analysis = load_movement_and_virtual_trade_analysis(
                session,
                score_threshold=args.score_threshold,
                holding_days=args.holding_days,
            )
        live_account = analysis["virtual_account"]
        if analysis["virtual_signals"].empty:
            live_account = {
                **live_account,
                "observation_status": "no_eligible_signals_or_data_quality_gate",
            }
        accounts = {"live_long_only": live_account}
    paths = [
        write_forward_shadow_snapshot(
            Path(args.output_dir) / account_name,
            account,
            as_of=observed_at,
            daily=args.daily,
        )
        for account_name, account in accounts.items()
    ]
    print("Forward-shadow snapshots:")
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
