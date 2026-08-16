from datetime import UTC, datetime
import argparse
import json
from pathlib import Path

from app.core.logging import configure_logging
from app.database.session import SessionLocal
from app.services.market_service import record_job
from app.analysis.demo_portfolio import run_demo_portfolio_environment
from app.backtest.audit import json_value
from app.core.config import get_settings
from app.services.backtest_service import run_real_walk_forward_backtest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a backtest or the isolated phase-4 demo.")
    parser.add_argument("--demo", action="store_true", help="Run only in-memory synthetic phase-4 evaluation")
    parser.add_argument(
        "--ledger-path",
        help="Optional JSON path for the demo account ledger; never writes to PostgreSQL",
    )
    parser.add_argument(
        "--validation-registry-path",
        help=(
            "Optional Git-ignored JSON registry. When supplied, unseen windows are "
            "claimed before evaluation and conflicting rule reuse is rejected."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging()
    started_at = datetime.now(UTC)
    if args.demo:
        if get_settings().market_data_mode != "demo":
            raise SystemExit("Synthetic backtest is demo-only. Set MARKET_DATA_MODE=demo.")
        result = run_demo_portfolio_environment(
            validation_registry_path=args.validation_registry_path
        )
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
                "validation_windows_claimed": int(
                    account["walk_forward"]["validation_claim_id"].notna().sum()
                )
                if "validation_claim_id" in account["walk_forward"]
                else 0,
            }
            for account_name, account in result["accounts"].items()
        }
        print(f"Phase-4 demo backtest summary: {summary}")
        return
    registry_path = args.validation_registry_path or "data/validation/live-windows.json"
    try:
        with SessionLocal() as session:
            details = run_real_walk_forward_backtest(
                session,
                validation_registry_path=registry_path,
            )
            record_job(session, "run_backtest", details["status"], started_at, details)
    except Exception as exc:
        try:
            with SessionLocal() as session:
                record_job(
                    session,
                    "run_backtest",
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
