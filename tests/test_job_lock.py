import json
from pathlib import Path

import pytest

from app.core.job_lock import (
    HEAVY_ANALYSIS_LOCK,
    SELECTED_FORWARD_LOCK,
    STANDARD_FORWARD_LOCK,
    TEMPORARY_FAILURE_EXIT_CODE,
    JobAlreadyRunning,
    exclusive_job_lock,
    prevent_concurrent_runs,
)


def test_same_lock_rejects_overlap_and_releases_after_exit(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_LOCK_DIR", str(tmp_path))

    with exclusive_job_lock(STANDARD_FORWARD_LOCK) as owner:
        assert owner["status"] == "running"
        with pytest.raises(JobAlreadyRunning) as caught:
            with exclusive_job_lock(STANDARD_FORWARD_LOCK):
                pass
        assert caught.value.owner["pid"] == owner["pid"]

    with exclusive_job_lock(STANDARD_FORWARD_LOCK):
        pass
    metadata = json.loads((tmp_path / f"{STANDARD_FORWARD_LOCK}.lock").read_text())
    assert metadata["status"] == "released"
    assert metadata["finished_at"]


def test_standard_and_selected_forward_locks_are_independent(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_LOCK_DIR", str(tmp_path))

    with exclusive_job_lock(STANDARD_FORWARD_LOCK):
        with exclusive_job_lock(SELECTED_FORWARD_LOCK):
            pass


def test_decorator_uses_retryable_exit_code_when_heavy_job_is_running(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("JOB_LOCK_DIR", str(tmp_path))

    @prevent_concurrent_runs(HEAVY_ANALYSIS_LOCK)
    def run():
        raise AssertionError("overlapping job must not execute")

    with exclusive_job_lock(HEAVY_ANALYSIS_LOCK):
        with pytest.raises(SystemExit) as caught:
            run()

    assert caught.value.code == TEMPORARY_FAILURE_EXIT_CODE
    assert "already running" in capsys.readouterr().err


def test_decorator_releases_all_locks_after_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_LOCK_DIR", str(tmp_path))

    @prevent_concurrent_runs(STANDARD_FORWARD_LOCK, HEAVY_ANALYSIS_LOCK)
    def fail():
        raise RuntimeError("job failed")

    with pytest.raises(RuntimeError, match="job failed"):
        fail()
    with exclusive_job_lock(STANDARD_FORWARD_LOCK):
        with exclusive_job_lock(HEAVY_ANALYSIS_LOCK):
            pass


def test_heavy_and_forward_job_entrypoints_use_expected_lock_scopes():
    for path in (
        "jobs/run_asset_analysis.py",
        "jobs/run_backtest.py",
        "jobs/run_mid_term_analysis.py",
        "jobs/run_short_term_analysis.py",
        "jobs/run_spillover_analysis.py",
    ):
        source = Path(path).read_text(encoding="utf-8")
        assert "@prevent_concurrent_runs(HEAVY_ANALYSIS_LOCK)" in source

    standard = Path("jobs/run_forward_shadow.py").read_text(encoding="utf-8")
    selected = Path("jobs/run_selected_universe_forward.py").read_text(
        encoding="utf-8"
    )
    assert (
        "@prevent_concurrent_runs(STANDARD_FORWARD_LOCK, HEAVY_ANALYSIS_LOCK)"
        in standard
    )
    assert (
        "@prevent_concurrent_runs(SELECTED_FORWARD_LOCK, HEAVY_ANALYSIS_LOCK)"
        in selected
    )
