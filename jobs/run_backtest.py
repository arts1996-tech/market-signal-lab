from datetime import UTC, datetime
import argparse
import json
from pathlib import Path

from app.core.logging import configure_logging
from app.database.session import SessionLocal
from app.services.market_service import record_job
from app.analysis.demo_portfolio import run_demo_portfolio_environment
from app.core.config import get_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a backtest or the isolated phase-4 demo.")
    parser.add_argument("--demo", action="store_true", help="Run only in-memory synthetic phase-4 evaluation")
    parser.add_argument(
        "--ledger-path",
        help="Optional JSON path for the demo account ledger; never writes to PostgreSQL",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging()
    started_at = datetime.now(UTC)
    if args.demo:
        if get_settings().market_data_mode != "demo":
            raise SystemExit("Synthetic backtest is demo-only. Set MARKET_DATA_MODE=demo.")
        result = run_demo_portfolio_environment()
        if args.ledger_path:
            path = Path(args.ledger_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            serializable = {
                "mode": result["mode"],
                "warning": result["warning"],
                "assumptions": result["assumptions"],
                "accounts": {
                    account_name: {
                        key: value.to_dict(orient="records")
                        if hasattr(value, "to_dict")
                        else value
                        for key, value in account.items()
                    }
                    for account_name, account in result["accounts"].items()
                },
            }
            path.write_text(
                json.dumps(serializable, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        summary = {
            account_name: {
                "initial_cash": account["initial_cash"],
                "cash": account["cash"],
                "equity": account["equity"],
                "realized_pnl": account["realized_pnl"],
                "unrealized_pnl": account["unrealized_pnl"],
                "maximum_drawdown": account["maximum_drawdown"],
                "transactions": len(account["transactions"]),
            }
            for account_name, account in result["accounts"].items()
        }
        print(f"Phase-4 demo backtest summary: {summary}")
        return
    with SessionLocal() as session:
        details = {"status": "placeholder", "note": "Backtest engine module is ready for signal rules."}
        record_job(session, "run_backtest", "success", started_at, details)
    print(f"Backtest summary: {details}")


if __name__ == "__main__":
    main()
