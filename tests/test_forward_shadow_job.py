import pandas as pd
import pytest

from jobs.run_forward_shadow import canonical_daily_decision_at, daily_preflight_reason


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
