from datetime import UTC, datetime
import argparse
from pathlib import Path

import pandas as pd

from app.analysis.demo_portfolio import run_demo_portfolio_environment
from app.analysis.decision_tracks import DECISION_TRACK_DELAYED
from app.backtest.shadow import write_forward_shadow_snapshot
from app.core.config import get_settings
from app.core.job_lock import (
    HEAVY_ANALYSIS_LOCK,
    STANDARD_FORWARD_LOCK,
    prevent_concurrent_runs,
)
from app.core.logging import configure_logging
from app.database.session import SessionLocal
from app.services.forward_account_ledger import (
    export_virtual_account_day,
    load_latest_forward_account_states,
    persist_forward_accounts,
)
from app.services.analysis_service import load_movement_and_virtual_trade_analysis
from app.services.forward_account_monitor import (
    audit_virtual_account_export,
    classify_forward_job_exception,
    new_attempt_id,
    output_capacity_status,
    record_forward_job_status,
)
from app.services.audit_integrity import AuditIntegrityError, verify_audit_chain
from app.services.forward_job_schedule import (
    canonical_daily_decision_at,
    daily_schedule_reason,
)


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


def daily_preflight_reason(
    output_dir: str | Path,
    account_names: list[str],
    observed_at: pd.Timestamp,
    *,
    not_before_jst: str | None = None,
    skip_existing_files: bool = True,
) -> str | None:
    schedule_reason = daily_schedule_reason(
        observed_at, not_before_jst=not_before_jst
    )
    if schedule_reason:
        return schedule_reason
    local = observed_at.tz_convert("Asia/Tokyo")
    date_label = local.strftime("%Y-%m-%d")
    paths = [Path(output_dir) / name / f"{date_label}.json" for name in account_names]
    if skip_existing_files and paths and all(path.exists() for path in paths):
        return f"all account snapshots for {date_label} are already frozen"
    return None
@prevent_concurrent_runs(STANDARD_FORWARD_LOCK, HEAVY_ANALYSIS_LOCK)
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
    attempt_id = new_attempt_id()
    started_at = datetime.now(UTC)
    if args.daily:
        reason = daily_preflight_reason(
            args.output_dir,
            expected_accounts,
            observed_at,
            not_before_jst=args.not_before_jst,
            skip_existing_files=args.demo,
        )
        if reason:
            if not args.demo:
                with SessionLocal() as session:
                    record_forward_job_status(
                        session,
                        status="started",
                        started_at=started_at,
                        attempt_id=attempt_id,
                        observed_at=observed_at,
                    )
                    record_forward_job_status(
                        session,
                        status="skipped",
                        started_at=started_at,
                        attempt_id=attempt_id,
                        observed_at=observed_at,
                        details={"reason": reason},
                    )
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
        try:
            with SessionLocal() as session:
                record_forward_job_status(
                    session,
                    status="started",
                    started_at=started_at,
                    attempt_id=attempt_id,
                    observed_at=observed_at,
                )
                capacity = output_capacity_status(args.output_dir)
                if capacity["status"] != "ok":
                    raise OSError(
                        28,
                        f"forward-shadow output capacity is insufficient: {capacity['free_bytes']}",
                    )
                audit_integrity = verify_audit_chain(args.output_dir)
                if audit_integrity["status"] == "invalid":
                    raise AuditIntegrityError(
                        "forward-account audit chain failed verification; "
                        "run jobs/verify_audit_integrity.py before writing"
                    )
                session_date = decision_at.tz_convert("Asia/Tokyo").date()
                if args.daily:
                    audits = [
                        audit_virtual_account_export(
                            session,
                            args.output_dir,
                            account_name=account_name,
                            decision_track=DECISION_TRACK_DELAYED,
                            session_date=session_date,
                        )
                        for account_name in ("short_term", "mid_term")
                    ]
                    if all(audit["status"] == "ok" for audit in audits):
                        reason = f"all DB-backed account snapshots for {session_date} are already frozen"
                        record_forward_job_status(
                            session,
                            status="skipped",
                            started_at=started_at,
                            attempt_id=attempt_id,
                            observed_at=observed_at,
                            details={"reason": reason},
                        )
                        print(f"Forward-shadow skipped: {reason}.")
                        return
                    invalid = [
                        audit["path"]
                        for audit in audits
                        if audit["status"] in {"json_modified", "json_unreadable"}
                    ]
                    if invalid:
                        raise FileExistsError(
                            f"immutable ledger export differs from PostgreSQL: {invalid}"
                        )
                    if all(audit["status"] in {"ok", "json_missing"} for audit in audits):
                        paths = [
                            export_virtual_account_day(
                                session,
                                args.output_dir,
                                account_name=account_name,
                                decision_track=DECISION_TRACK_DELAYED,
                                session_date=session_date,
                            )
                            for account_name in ("short_term", "mid_term")
                        ]
                        record_forward_job_status(
                            session,
                            status="success",
                            started_at=started_at,
                            attempt_id=attempt_id,
                            observed_at=observed_at,
                            details={
                                "recorded_session_date": session_date.isoformat(),
                                "accounts": ["mid_term", "short_term"],
                                "paths": [str(path) for path in paths],
                                "repaired_missing_exports": True,
                                "output_capacity": capacity,
                            },
                        )
                        print("Forward-shadow repaired missing DB-backed JSON exports.")
                        for path in paths:
                            print(path)
                        return
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
                record_forward_job_status(
                    session,
                    status="success",
                    started_at=started_at,
                    attempt_id=attempt_id,
                    observed_at=observed_at,
                    details={
                        "recorded_session_date": session_date.isoformat(),
                        "accounts": sorted(persisted["accounts"]),
                        "paths": [str(path) for path in paths],
                        "output_capacity": capacity,
                    },
                )
        except Exception as exc:
            failure_category = classify_forward_job_exception(exc)
            try:
                with SessionLocal() as failure_session:
                    record_forward_job_status(
                        failure_session,
                        status="error",
                        started_at=started_at,
                        attempt_id=attempt_id,
                        observed_at=observed_at,
                        details={
                            "failure_category": failure_category,
                            "error_type": type(exc).__name__,
                        },
                    )
            except Exception:
                pass
            raise
    print("Forward-shadow snapshots:")
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
