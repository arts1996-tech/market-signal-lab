"""Advance only explicitly enabled selected-universe delayed research accounts."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json

import pandas as pd

from app.analysis.decision_tracks import DECISION_TRACK_DELAYED
from app.backtest.audit import json_value
from app.core.job_lock import (
    HEAVY_ANALYSIS_LOCK,
    SELECTED_FORWARD_LOCK,
    prevent_concurrent_runs,
)
from app.core.logging import configure_logging
from app.database.session import SessionLocal
from app.services.analysis_service import load_movement_and_virtual_trade_analysis
from app.services.forward_job_schedule import (
    canonical_daily_decision_at,
    daily_schedule_reason,
)
from app.services.market_service import record_job
from app.services.selected_forward_activation_service import (
    explicitly_enabled_selections,
)
from app.services.selected_universe_forward_account_service import (
    advance_selected_universe_forward_accounts,
)


JOB_NAME = "run_selected_universe_forward"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observed-at", help="Optional ISO-8601 replay time")
    parser.add_argument("--not-before-jst", default="18:30")
    parser.add_argument("--score-threshold", type=int, default=70)
    parser.add_argument("--holding-days", type=int, default=5)
    return parser.parse_args()


@prevent_concurrent_runs(SELECTED_FORWARD_LOCK, HEAVY_ANALYSIS_LOCK)
def main() -> None:
    args = parse_args()
    configure_logging()
    started_at = datetime.now(UTC)
    observed_at = pd.Timestamp(args.observed_at or started_at)
    if observed_at.tzinfo is None:
        observed_at = observed_at.tz_localize("UTC")
    else:
        observed_at = observed_at.tz_convert("UTC")
    reason = daily_schedule_reason(
        observed_at, not_before_jst=args.not_before_jst
    )
    if reason:
        with SessionLocal() as session:
            details = {"status": "skipped", "reason": reason}
            record_job(session, JOB_NAME, "skipped", started_at, details)
        print(json.dumps(details, ensure_ascii=False))
        return

    decision_at = canonical_daily_decision_at(observed_at)
    try:
        with SessionLocal() as session:
            selections = explicitly_enabled_selections(
                session, as_of=decision_at.to_pydatetime()
            )
            if not selections:
                details = {
                    "status": "skipped",
                    "reason": "no_explicitly_enabled_selection_versions",
                    "recorded_session_date": decision_at.tz_convert("Asia/Tokyo")
                    .date()
                    .isoformat(),
                }
                record_job(session, JOB_NAME, "skipped", started_at, details)
                print(json.dumps(details, ensure_ascii=False))
                return

            analysis = load_movement_and_virtual_trade_analysis(
                session,
                score_threshold=args.score_threshold,
                holding_days=args.holding_days,
                signal_as_of=decision_at,
                decision_track=DECISION_TRACK_DELAYED,
            )
            generation = analysis["signal_generation"]
            results = []
            for selection in selections:
                result = advance_selected_universe_forward_accounts(
                    session,
                    selection_id=selection.id,
                    signals=generation["signals"],
                    prices=analysis["japan_prices"],
                    observation=generation["decision_observation"],
                    decisions=generation["decisions"],
                )
                results.append(
                    {
                        "selection_id": selection.id,
                        "selection_key": selection.selection_key,
                        "selection_version": selection.version,
                        "composition_hash": selection.composition_hash,
                        "accounts": sorted(result["ledger"]["accounts"]),
                        "new_entries_allowed": result["allowed_selection"][
                            "new_entries_allowed"
                        ],
                    }
                )
            details = {
                "status": "success",
                "decision_track": DECISION_TRACK_DELAYED,
                "recorded_session_date": decision_at.tz_convert("Asia/Tokyo")
                .date()
                .isoformat(),
                "enabled_selection_count": len(selections),
                "results": results,
                "warning": "research_only_no_orders",
            }
            record_job(session, JOB_NAME, "success", started_at, json_value(details))
    except Exception as exc:
        try:
            with SessionLocal() as session:
                record_job(
                    session,
                    JOB_NAME,
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
