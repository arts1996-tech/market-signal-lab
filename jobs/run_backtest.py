from datetime import UTC, datetime
import argparse

from app.core.logging import configure_logging
from app.database.session import SessionLocal
from app.services.market_service import record_job
from app.analysis.virtual_trading import (
    build_virtual_trades,
    generate_demo_phase4_data,
    simulate_virtual_account,
)
from app.core.config import get_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a backtest or the isolated phase-4 demo.")
    parser.add_argument("--demo", action="store_true", help="Run only in-memory synthetic phase-4 evaluation")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging()
    started_at = datetime.now(UTC)
    if args.demo:
        if get_settings().market_data_mode != "demo":
            raise SystemExit("Synthetic backtest is demo-only. Set MARKET_DATA_MODE=demo.")
        index_prices, japan_prices = generate_demo_phase4_data()
        short_trades = build_virtual_trades(index_prices, japan_prices, score_threshold=50, holding_days=5, min_observations=30)
        mid_trades = build_virtual_trades(index_prices, japan_prices, score_threshold=50, holding_days=20, min_observations=30)
        result = {
            "mode": "demo_only",
            "warning": "合成データによる検証用であり、投資判断・実績には使用しません。",
            "short_term": simulate_virtual_account(short_trades, account_name="short_term"),
            "mid_term": simulate_virtual_account(mid_trades, account_name="mid_term"),
        }
        result["short_term"].pop("trades", None)
        result["mid_term"].pop("trades", None)
        print(f"Phase-4 demo backtest summary: {result}")
        return
    with SessionLocal() as session:
        details = {"status": "placeholder", "note": "Backtest engine module is ready for signal rules."}
        record_job(session, "run_backtest", "success", started_at, details)
    print(f"Backtest summary: {details}")


if __name__ == "__main__":
    main()
