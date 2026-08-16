from datetime import UTC, datetime, time
import argparse
from pathlib import Path

import pandas as pd

from app.analysis.demo_portfolio import run_demo_portfolio_environment
from app.analysis.decision_tracks import DECISION_TRACK_DELAYED
from app.analysis.market_calendar import is_exchange_session
from app.backtest.shadow import write_forward_shadow_snapshot
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.database.session import SessionLocal
from app.services.forward_account_ledger import (
    export_virtual_account_day,
    load_latest_forward_account_states,
    persist_forward_accounts,
)
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
    parser.add_argument(
        "--not-before-jst",
        help="Optional HH:MM cutoff. Daily recording exits successfully before this JST time.",
    )
    return parser.parse_args()


def _parse_time(value: str | None) -> time | None:
    if value is None:
        return None
    try:
        hour_text, minute_text = value.split(":", maxsplit=1)
        parsed = time(hour=int(hour_text), minute=int(minute_text))
    except (TypeError, ValueError) as exc:
        raise ValueError("not-before-jst must use HH:MM") from exc
    return parsed


def daily_preflight_reason(
    output_dir: str | Path,
    account_names: list[str],
    observed_at: pd.Timestamp,
    *,
    not_before_jst: str | None = None,
) -> str | None:
    local = observed_at.tz_convert("Asia/Tokyo")
    local_date = local.normalize()
    if not is_exchange_session(local_date, "XTKS"):
        return f"{local_date.date()} is not a Tokyo Stock Exchange session"
    cutoff = _parse_time(not_before_jst)
    if cutoff is not None and local.time().replace(tzinfo=None) < cutoff:
        return f"current JST time is before the {not_before_jst} recording cutoff"
    date_label = local.strftime("%Y-%m-%d")
    paths = [Path(output_dir) / name / f"{date_label}.json" for name in account_names]
    if paths and all(path.exists() for path in paths):
        return f"all account snapshots for {date_label} are already frozen"
    return None


def canonical_daily_decision_at(observed_at: pd.Timestamp) -> pd.Timestamp:
    """Use one stable 18:30 JST decision timestamp across same-day retries."""

    local = observed_at.tz_convert("Asia/Tokyo")
    canonical = local.normalize() + pd.Timedelta(hours=18, minutes=30)
    return canonical.tz_convert("UTC")


def main() -> None:
    args = parse_args()
    configure_logging()
    observed_at = pd.Timestamp(args.observed_at or datetime.now(UTC))
    if observed_at.tzinfo is None:
        observed_at = observed_at.tz_localize("UTC")
    else:
        observed_at = observed_at.tz_convert("UTC")
    settings = get_settings()
    expected_accounts = (
        ["short_term", "mid_term"]
        if args.demo
        else [
            f"short_term/{DECISION_TRACK_DELAYED}",
            f"mid_term/{DECISION_TRACK_DELAYED}",
        ]
    )
    if args.daily:
        reason = daily_preflight_reason(
            args.output_dir,
            expected_accounts,
            observed_at,
            not_before_jst=args.not_before_jst,
        )
        if reason:
            print(f"Forward-shadow skipped: {reason}.")
            return
    if args.demo:
        if settings.market_data_mode != "demo":
            raise SystemExit("Synthetic forward recording requires MARKET_DATA_MODE=demo.")
        environment = run_demo_portfolio_environment()
        accounts = environment["accounts"]
        paths = [
            write_forward_shadow_snapshot(
                Path(args.output_dir) / account_name,
                account,
                as_of=observed_at,
                daily=args.daily,
            )
            for account_name, account in accounts.items()
        ]
    else:
        if settings.market_data_mode == "demo":
            raise SystemExit("Real-data forward recording is disabled in demo mode.")
        decision_at = canonical_daily_decision_at(observed_at) if args.daily else observed_at
        with SessionLocal() as session:
            previous_states = load_latest_forward_account_states(
                session, DECISION_TRACK_DELAYED
            )
            analysis = load_movement_and_virtual_trade_analysis(
                session,
                score_threshold=args.score_threshold,
                holding_days=args.holding_days,
                signal_as_of=decision_at,
                previous_forward_states=previous_states or None,
                decision_track=DECISION_TRACK_DELAYED,
            )
            signal_generation = analysis["signal_generation"]
            persisted = persist_forward_accounts(
                session,
                analysis["forward_accounts"],
                observation=signal_generation["decision_observation"],
                decisions=signal_generation["decisions"],
            )
            session.commit()
            paths = [
                export_virtual_account_day(
                    session,
                    args.output_dir,
                    account_name=account_name,
                    decision_track=DECISION_TRACK_DELAYED,
                    session_date=record["session_date"],
                )
                for account_name, record in persisted["accounts"].items()
            ]
    print("Forward-shadow snapshots:")
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
