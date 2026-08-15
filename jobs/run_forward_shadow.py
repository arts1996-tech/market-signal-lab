from datetime import UTC, datetime
import argparse
from pathlib import Path

from app.analysis.demo_portfolio import run_demo_portfolio_environment
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging()
    observed_at = datetime.now(UTC)
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
        if analysis["virtual_signals"].empty:
            raise SystemExit(
                "No point-in-time signals passed the current real-data quality gate; "
                "nothing was recorded."
            )
        accounts = {"live_long_only": analysis["virtual_account"]}
    paths = [
        write_forward_shadow_snapshot(
            Path(args.output_dir) / account_name,
            account,
            as_of=observed_at,
        )
        for account_name, account in accounts.items()
    ]
    print("Forward-shadow snapshots:")
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
