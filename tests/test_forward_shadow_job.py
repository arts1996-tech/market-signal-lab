from datetime import UTC, datetime
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy.exc import OperationalError

from jobs.run_forward_shadow import canonical_daily_decision_at, daily_preflight_reason
from app.services import forward_account_monitor as monitor


def test_daily_preflight_skips_before_evening_cutoff(tmp_path):
    reason = daily_preflight_reason(
        tmp_path,
        ["live_long_only"],
        pd.Timestamp("2026-08-17T08:00:00Z"),
        not_before_jst="18:30",
    )

    assert "before" in reason


def test_daily_preflight_allows_first_evening_attempt(tmp_path):
    reason = daily_preflight_reason(
        tmp_path,
        ["live_long_only"],
        pd.Timestamp("2026-08-17T10:00:00Z"),
        not_before_jst="18:30",
    )

    assert reason is None


def test_daily_preflight_skips_when_all_accounts_are_already_frozen(tmp_path):
    snapshot = tmp_path / "live_long_only" / "2026-08-17.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text("{}", encoding="utf-8")

    reason = daily_preflight_reason(
        tmp_path,
        ["live_long_only"],
        pd.Timestamp("2026-08-17T10:00:00Z"),
        not_before_jst="18:30",
    )

    assert "already frozen" in reason


def test_daily_retries_share_one_canonical_decision_timestamp():
    first = canonical_daily_decision_at(pd.Timestamp("2026-08-17T09:30:00Z"))
    retry = canonical_daily_decision_at(pd.Timestamp("2026-08-17T13:30:00Z"))

    assert first == retry == pd.Timestamp("2026-08-17T09:30:00Z")


def test_daily_preflight_rejects_invalid_cutoff(tmp_path):
    with pytest.raises(ValueError, match="HH:MM"):
        daily_preflight_reason(
            tmp_path,
            ["live_long_only"],
            pd.Timestamp("2026-08-17T10:00:00Z"),
            not_before_jst="evening",
        )


def test_expected_forward_session_waits_for_cutoff():
    before = monitor.expected_forward_session_date("2026-08-17T08:00:00Z")
    after = monitor.expected_forward_session_date("2026-08-17T10:00:00Z")

    assert before.isoformat() == "2026-08-14"
    assert after.isoformat() == "2026-08-17"


def test_output_capacity_distinguishes_insufficient_storage(tmp_path):
    usage = SimpleNamespace(total=1_000, used=960, free=40)

    result = monitor.output_capacity_status(
        tmp_path,
        usage_loader=lambda _: usage,
        min_free_bytes=100,
        max_used_ratio=0.95,
    )

    assert result["status"] == "insufficient"
    assert result["used_ratio"] == pytest.approx(0.96)


def test_host_attempt_log_preserves_docker_and_database_categories(tmp_path):
    path = tmp_path / "attempts.tsv"
    path.write_text(
        "2026-08-17T09:30:00Z\thost-a\terror\tdocker_unavailable\t75\n"
        "2026-08-17T11:30:00Z\thost-b\terror\tdatabase_unavailable\t1\n",
        encoding="utf-8",
    )

    attempts = monitor.read_host_attempts(path)

    assert [attempt["category"] for attempt in attempts] == [
        "docker_unavailable",
        "database_unavailable",
    ]


def test_export_audit_detects_missing_and_modified_json(monkeypatch, tmp_path):
    payload = {"daily_state": {"input_hash": "frozen"}}
    monkeypatch.setattr(monitor, "build_virtual_account_day_export", lambda *a, **k: payload)
    expected_path = tmp_path / "short_term" / "delayed_historical" / "2026-08-17.json"

    missing = monitor.audit_virtual_account_export(
        object(),
        tmp_path,
        account_name="short_term",
        decision_track="delayed_historical",
        session_date=pd.Timestamp("2026-08-17").date(),
    )
    expected_path.parent.mkdir(parents=True)
    expected_path.write_text("{}", encoding="utf-8")
    modified = monitor.audit_virtual_account_export(
        object(),
        tmp_path,
        account_name="short_term",
        decision_track="delayed_historical",
        session_date=pd.Timestamp("2026-08-17").date(),
    )

    assert missing["status"] == "json_missing"
    assert modified["status"] == "json_modified"


def test_monitor_warns_after_three_failed_retries(monkeypatch, tmp_path):
    session_date = pd.Timestamp("2026-08-17").date()
    states = [
        ("short_term", SimpleNamespace(session_date=session_date)),
        ("mid_term", SimpleNamespace(session_date=session_date)),
    ]
    failures = [
        SimpleNamespace(
            status="error",
            started_at=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
            details={
                "attempt_id": f"attempt-{index}",
                "session_date": "2026-08-17",
                "failure_category": "execution_failed",
            },
        )
        for index in range(3)
    ]

    class FakeSession:
        def execute(self, _query):
            return SimpleNamespace(all=lambda: states)

        def scalars(self, _query):
            return failures

    monkeypatch.setattr(monitor, "expected_forward_session_date", lambda *_a, **_k: session_date)
    monkeypatch.setattr(monitor, "_expected_sessions", lambda *_a, **_k: [session_date])
    monkeypatch.setattr(monitor, "latest_successful_job_run", lambda *_a, **_k: None)
    monkeypatch.setattr(
        monitor,
        "audit_virtual_account_export",
        lambda *_a, **_k: {"status": "ok", "path": "snapshot.json"},
    )
    monkeypatch.setattr(
        monitor,
        "output_capacity_status",
        lambda *_a, **_k: {"status": "ok"},
    )

    result = monitor.build_forward_account_monitor(
        FakeSession(),
        output_dir=tmp_path,
        observed_at="2026-08-17T14:00:00Z",
        host_attempt_log=tmp_path / "missing.tsv",
    )

    assert result["failed_attempts_for_expected_session"] == 3
    assert "three_retries_failed" in result["warnings"]


def test_forward_failure_classification_keeps_database_separate():
    error = OperationalError("SELECT 1", {}, RuntimeError("offline"))

    assert monitor.classify_forward_job_exception(error) == "database_unavailable"
