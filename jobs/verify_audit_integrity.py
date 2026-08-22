"""Verify the forward-account audit chain without changing investment results."""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.database.repositories import insert_job_run
from app.database.session import SessionLocal
from app.services.audit_integrity import (
    discover_formal_exports,
    initialize_audit_chain,
    verify_audit_chain,
)
from app.services.forward_account_monitor import audit_virtual_account_export


AUDIT_VERIFICATION_JOB = "verify_audit_integrity"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect modified, missing, untracked, reordered or removed audit records."
    )
    parser.add_argument(
        "--output-dir",
        default="data/forward_shadow",
        help="Root containing formal forward-account JSON exports.",
    )
    parser.add_argument(
        "--initialize-existing",
        action="store_true",
        help=(
            "Establish a chain for existing formal exports only after each file matches "
            "the PostgreSQL source of truth."
        ),
    )
    return parser.parse_args()


def validate_existing_exports(
    session: Session,
    output_dir: str | Path,
    paths: list[Path],
) -> list[dict]:
    root = Path(output_dir)
    problems: list[dict] = []
    for path in paths:
        relative = path.relative_to(root)
        try:
            session_date = date.fromisoformat(relative.stem)
        except ValueError:
            problems.append(
                {"category": "invalid_export_date", "path": relative.as_posix()}
            )
            continue
        audit = audit_virtual_account_export(
            session,
            root,
            account_name=relative.parts[0],
            decision_track=relative.parts[1],
            session_date=session_date,
        )
        if audit["status"] != "ok":
            problems.append(
                {
                    "category": "baseline_db_mismatch",
                    "path": relative.as_posix(),
                    "db_audit_status": audit["status"],
                }
            )
    return problems


def record_verification(
    session: Session,
    *,
    started_at: datetime,
    result: dict,
    initialized: bool,
) -> None:
    status = "success" if result["status"] in {"ok", "empty"} else "error"
    insert_job_run(
        session,
        job_name=AUDIT_VERIFICATION_JOB,
        status=status,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        details={
            "audit_status": result["status"],
            "chain_version": result["chain_version"],
            "record_count": result["record_count"],
            "checked_file_count": result["checked_file_count"],
            "untracked_file_count": result["untracked_file_count"],
            "anomaly_count": result["anomaly_count"],
            "anomalies": result["anomalies"],
            "last_sequence": result["last_sequence"],
            "last_record_hash": result["last_record_hash"],
            "initialized_existing": initialized,
            "separate_from_investment_results": True,
        },
    )
    session.commit()


def main() -> None:
    args = parse_args()
    started_at = datetime.now(UTC)
    initialized = False
    with SessionLocal() as session:
        if args.initialize_existing:
            paths = discover_formal_exports(args.output_dir)
            problems = validate_existing_exports(session, args.output_dir, paths)
            if problems:
                result = verify_audit_chain(args.output_dir)
                result = {
                    **result,
                    "status": "invalid",
                    "anomaly_count": result["anomaly_count"] + len(problems),
                    "anomalies": [*result["anomalies"], *problems],
                }
                record_verification(
                    session,
                    started_at=started_at,
                    result=result,
                    initialized=False,
                )
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
                raise SystemExit(1)
            initialize_audit_chain(args.output_dir, paths)
            initialized = True
        result = verify_audit_chain(args.output_dir)
        record_verification(
            session,
            started_at=started_at,
            result=result,
            initialized=initialized,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["status"] == "invalid":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
