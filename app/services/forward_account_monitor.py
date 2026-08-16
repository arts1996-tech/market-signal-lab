"""Operational monitoring for the append-only forward virtual-account ledger."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
import errno
from pathlib import Path
import shutil
from typing import Any, Callable
from uuid import uuid4

import pandas as pd
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.analysis.decision_tracks import DECISION_TRACK_DELAYED
from app.analysis.market_calendar import exchange_calendar, is_exchange_session
from app.database.models import JobRun, VirtualAccount, VirtualAccountDailyState
from app.database.repositories import insert_job_run, latest_successful_job_run
from app.database.session import SessionLocal
from app.services.forward_account_ledger import (
    build_virtual_account_day_export,
    serialize_virtual_account_day_export,
)


FORWARD_JOB_NAME = "run_forward_shadow"
EXPECTED_FORWARD_ACCOUNTS = ("short_term", "mid_term")
HOST_ATTEMPT_LOG = Path("logs/forward-shadow-host-attempts.tsv")
MIN_OUTPUT_FREE_BYTES = 512 * 1024 * 1024
MAX_OUTPUT_USED_RATIO = 0.95
JST = "Asia/Tokyo"


def _utc_timestamp(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def new_attempt_id() -> str:
    return uuid4().hex


def record_forward_job_status(
    session: Session,
    *,
    status: str,
    started_at: datetime,
    attempt_id: str,
    observed_at: Any,
    details: dict | None = None,
) -> None:
    """Append one lifecycle status; a start row remains visible after crashes."""

    observed = _utc_timestamp(observed_at)
    payload = {
        "attempt_id": attempt_id,
        "observed_at": observed.isoformat(),
        "session_date": observed.tz_convert(JST).date().isoformat(),
        "decision_track": DECISION_TRACK_DELAYED,
        **(details or {}),
    }
    insert_job_run(
        session,
        job_name=FORWARD_JOB_NAME,
        status=status,
        started_at=started_at,
        finished_at=None if status == "started" else datetime.now(UTC),
        details=payload,
    )
    session.commit()


def classify_forward_job_exception(exc: BaseException) -> str:
    if isinstance(exc, FileExistsError):
        return "json_modified"
    if isinstance(exc, SQLAlchemyError):
        return "database_unavailable"
    if isinstance(exc, OSError) and exc.errno in {errno.ENOSPC, errno.EDQUOT}:
        return "output_capacity_insufficient"
    return "execution_failed"


def output_capacity_status(
    output_dir: str | Path,
    *,
    usage_loader: Callable[[str | Path], Any] = shutil.disk_usage,
    min_free_bytes: int = MIN_OUTPUT_FREE_BYTES,
    max_used_ratio: float = MAX_OUTPUT_USED_RATIO,
) -> dict:
    path = Path(output_dir)
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    usage = usage_loader(probe)
    used_ratio = usage.used / usage.total if usage.total else 1.0
    sufficient = usage.free >= min_free_bytes and used_ratio < max_used_ratio
    return {
        "status": "ok" if sufficient else "insufficient",
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "used_ratio": used_ratio,
        "minimum_free_bytes": min_free_bytes,
    }


def expected_forward_session_date(
    observed_at: Any,
    *,
    not_before_jst: str = "18:30",
) -> date | None:
    """Return the latest XTKS session whose recording deadline has arrived."""

    local = _utc_timestamp(observed_at).tz_convert(JST)
    hour, minute = (int(part) for part in not_before_jst.split(":", maxsplit=1))
    cutoff = time(hour=hour, minute=minute)
    include_today = (
        is_exchange_session(local.normalize(), "XTKS")
        and local.time().replace(tzinfo=None) >= cutoff
    )
    end = local.normalize() if include_today else local.normalize() - pd.Timedelta(days=1)
    end = end.tz_localize(None)
    start = end - pd.Timedelta(days=45)
    sessions = exchange_calendar("XTKS").sessions_in_range(start, end)
    return sessions[-1].date() if len(sessions) else None


def audit_virtual_account_export(
    session: Session,
    output_dir: str | Path,
    *,
    account_name: str,
    decision_track: str,
    session_date: date,
) -> dict:
    path = Path(output_dir) / account_name / decision_track / f"{session_date.isoformat()}.json"
    try:
        payload = build_virtual_account_day_export(
            session,
            account_name=account_name,
            decision_track=decision_track,
            session_date=session_date,
        )
    except LookupError:
        return {"status": "db_state_missing", "path": str(path)}
    if not path.exists():
        return {"status": "json_missing", "path": str(path)}
    try:
        actual = path.read_text(encoding="utf-8")
    except OSError:
        return {"status": "json_unreadable", "path": str(path)}
    expected = serialize_virtual_account_day_export(payload)
    if actual != expected:
        return {"status": "json_modified", "path": str(path)}
    return {"status": "ok", "path": str(path)}


def read_host_attempts(path: str | Path = HOST_ATTEMPT_LOG, limit: int = 100) -> list[dict]:
    log_path = Path(path)
    if not log_path.exists():
        return []
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return [{"status": "error", "category": "host_attempt_log_unreadable"}]
    attempts = []
    for line in lines:
        parts = line.split("\t", maxsplit=4)
        if len(parts) != 5:
            continue
        timestamp, attempt_id, status, category, exit_code = parts
        try:
            observed = _utc_timestamp(timestamp)
        except (TypeError, ValueError):
            continue
        attempts.append(
            {
                "observed_at": observed.isoformat(),
                "attempt_id": attempt_id,
                "status": status,
                "category": category,
                "exit_code": int(exit_code) if exit_code.lstrip("-").isdigit() else None,
            }
        )
    return attempts


def _expected_sessions(start: date, end: date) -> list[date]:
    if start > end:
        return []
    sessions = exchange_calendar("XTKS").sessions_in_range(start, end)
    return [value.date() for value in sessions]


def build_forward_account_monitor(
    session: Session,
    *,
    output_dir: str | Path = "data/forward_shadow",
    observed_at: Any | None = None,
    host_attempt_log: str | Path = HOST_ATTEMPT_LOG,
) -> dict:
    observed = _utc_timestamp(observed_at or datetime.now(UTC))
    expected_date = expected_forward_session_date(observed)
    capacity = output_capacity_status(output_dir)
    rows = session.execute(
        select(VirtualAccount.account_name, VirtualAccountDailyState)
        .join(
            VirtualAccountDailyState,
            VirtualAccountDailyState.account_id == VirtualAccount.id,
        )
        .where(
            VirtualAccount.account_name.in_(EXPECTED_FORWARD_ACCOUNTS),
            VirtualAccountDailyState.decision_track == DECISION_TRACK_DELAYED,
        )
        .order_by(VirtualAccountDailyState.session_date)
    ).all()
    states_by_account: dict[str, dict[date, VirtualAccountDailyState]] = {
        name: {} for name in EXPECTED_FORWARD_ACCOUNTS
    }
    for account_name, state in rows:
        states_by_account[account_name][state.session_date] = state
    job_runs = list(
        session.scalars(
            select(JobRun)
            .where(JobRun.job_name == FORWARD_JOB_NAME)
            .order_by(JobRun.started_at.desc())
        )
    )
    host_attempts = read_host_attempts(host_attempt_log)

    observed_dates = sorted(
        {session_date for states in states_by_account.values() for session_date in states}
    )
    if expected_date is None:
        expected_dates: list[date] = []
    else:
        completed_attempt_dates = []
        for job in job_runs:
            if job.status not in {"success", "error"} or not isinstance(job.details, dict):
                continue
            try:
                completed_attempt_dates.append(date.fromisoformat(job.details["session_date"]))
            except (KeyError, TypeError, ValueError):
                continue
        host_attempt_dates = []
        for attempt in host_attempts:
            if attempt.get("status") != "error" or not attempt.get("observed_at"):
                continue
            host_attempt_dates.append(
                _utc_timestamp(attempt["observed_at"]).tz_convert(JST).date()
            )
        start_candidates = [
            value
            for value in observed_dates + completed_attempt_dates + host_attempt_dates
            if value <= expected_date
        ]
        first_date = min(start_candidates, default=expected_date)
        expected_dates = _expected_sessions(first_date, expected_date)
    missing_dates = [
        session_date
        for session_date in expected_dates
        if any(session_date not in states_by_account[name] for name in EXPECTED_FORWARD_ACCOUNTS)
    ]

    account_status = []
    export_problems = []
    for account_name in EXPECTED_FORWARD_ACCOUNTS:
        states = states_by_account[account_name]
        latest_date = max(states, default=None)
        audit = (
            audit_virtual_account_export(
                session,
                output_dir,
                account_name=account_name,
                decision_track=DECISION_TRACK_DELAYED,
                session_date=expected_date,
            )
            if expected_date is not None
            else {"status": "not_due", "path": None}
        )
        account_status.append(
            {
                "account_name": account_name,
                "expected_session_date": expected_date.isoformat() if expected_date else None,
                "recorded": expected_date in states if expected_date else False,
                "latest_recorded_session": latest_date.isoformat() if latest_date else None,
                "json_status": audit["status"],
                "json_path": audit["path"],
            }
        )
        if audit["status"] not in {"ok", "not_due", "db_state_missing"}:
            export_problems.append(
                {"account_name": account_name, "session_date": expected_date, **audit}
            )
        for recorded_date in states:
            if recorded_date == expected_date:
                continue
            historical_audit = audit_virtual_account_export(
                session,
                output_dir,
                account_name=account_name,
                decision_track=DECISION_TRACK_DELAYED,
                session_date=recorded_date,
            )
            if historical_audit["status"] != "ok":
                export_problems.append(
                    {
                        "account_name": account_name,
                        "session_date": recorded_date,
                        **historical_audit,
                    }
                )

    latest_success = latest_successful_job_run(session, FORWARD_JOB_NAME)
    expected_label = expected_date.isoformat() if expected_date else None
    day_runs = [
        job
        for job in job_runs
        if isinstance(job.details, dict) and job.details.get("session_date") == expected_label
    ]
    failed_attempts = [job for job in day_runs if job.status == "error"]
    terminal_attempt_ids = {
        job.details.get("attempt_id")
        for job in day_runs
        if job.status in {"success", "skipped", "error"} and isinstance(job.details, dict)
    }
    stale_started = [
        job
        for job in day_runs
        if job.status == "started"
        and job.details.get("attempt_id") not in terminal_attempt_ids
        and observed.to_pydatetime() - job.started_at > timedelta(minutes=30)
    ]
    host_day_attempts = [
        attempt
        for attempt in host_attempts
        if expected_date is not None
        and _utc_timestamp(attempt["observed_at"]).tz_convert(JST).date() == expected_date
    ]
    host_infrastructure_failures = [
        attempt
        for attempt in host_day_attempts
        if attempt["status"] == "error"
        and attempt.get("category") in {"docker_unavailable", "database_unavailable"}
    ]
    failed_attempt_count = len(failed_attempts) + len(host_infrastructure_failures)
    failure_categories = sorted(
        {
            str(job.details.get("failure_category"))
            for job in failed_attempts
            if job.details.get("failure_category")
        }
        | {
            str(attempt["category"])
            for attempt in host_day_attempts
            if attempt["status"] == "error" and attempt.get("category")
        }
    )
    warnings = []
    if missing_dates:
        warnings.append("missing_business_sessions")
    if failed_attempt_count >= 3:
        warnings.append("three_retries_failed")
    if stale_started:
        warnings.append("unfinished_job_attempt")
    if capacity["status"] != "ok":
        warnings.append("output_capacity_insufficient")
    if export_problems:
        warnings.append("json_audit_problem")
    warnings.extend(category for category in failure_categories if category not in warnings)
    warnings = list(dict.fromkeys(warnings))

    return {
        "database_available": True,
        "status": "warning" if warnings else "ok",
        "checked_at": observed.isoformat(),
        "decision_track": DECISION_TRACK_DELAYED,
        "expected_session_date": expected_label,
        "accounts": account_status,
        "latest_success_at": (
            _utc_timestamp(latest_success.finished_at or latest_success.started_at).isoformat()
            if latest_success
            else None
        ),
        "latest_success_session": (
            latest_success.details.get("session_date")
            if latest_success and isinstance(latest_success.details, dict)
            else None
        ),
        "failed_attempts_for_expected_session": failed_attempt_count,
        "stale_started_attempts": len(stale_started),
        "missing_session_dates": [value.isoformat() for value in missing_dates],
        "output_capacity": capacity,
        "export_problems": export_problems,
        "failure_categories": failure_categories,
        "host_attempts": host_day_attempts,
        "warnings": warnings,
    }


def load_forward_account_monitor(
    *,
    output_dir: str | Path = "data/forward_shadow",
    observed_at: Any | None = None,
    host_attempt_log: str | Path = HOST_ATTEMPT_LOG,
) -> dict:
    """Load monitor data while keeping a DB outage visible to the dashboard."""

    observed = _utc_timestamp(observed_at or datetime.now(UTC))
    try:
        with SessionLocal() as session:
            return build_forward_account_monitor(
                session,
                output_dir=output_dir,
                observed_at=observed,
                host_attempt_log=host_attempt_log,
            )
    except SQLAlchemyError:
        host_attempts = read_host_attempts(host_attempt_log)
        expected_date = expected_forward_session_date(observed)
        return {
            "database_available": False,
            "status": "warning",
            "checked_at": observed.isoformat(),
            "decision_track": DECISION_TRACK_DELAYED,
            "expected_session_date": expected_date.isoformat() if expected_date else None,
            "accounts": [],
            "latest_success_at": None,
            "latest_success_session": None,
            "failed_attempts_for_expected_session": 0,
            "stale_started_attempts": 0,
            "missing_session_dates": [],
            "output_capacity": output_capacity_status(output_dir),
            "export_problems": [],
            "failure_categories": ["database_unavailable"],
            "host_attempts": host_attempts,
            "warnings": ["database_unavailable"],
        }
