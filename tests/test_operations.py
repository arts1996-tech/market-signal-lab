from datetime import date

from app.analysis.market_calendar import exchange_calendar
from jobs.check_operations import (
    contiguous_history_counts,
    recent_exchange_sessions,
    recent_window_progress,
)


def test_operations_history_readiness_uses_latest_contiguous_exchange_sessions():
    old_dates = exchange_calendar("XTKS").sessions_in_range("2024-01-01", "2024-03-31")[:20]
    recent_dates = exchange_calendar("XTKS").sessions_in_range("2026-04-01", "2026-05-31")[:15]
    rows = [
        {"asset_id": "gap", "session_date": value.date()}
        for value in old_dates.append(recent_dates)
    ]
    rows.extend(
        {"asset_id": "ready", "session_date": value.date()}
        for value in exchange_calendar("XTKS").sessions_in_range(
            "2026-01-01", "2026-03-31"
        )[:30]
    )

    result = contiguous_history_counts(rows)

    assert result["ge_30"] == 1
    assert result["ge_20"] == 1
    assert result["ge_10"] == 2
    assert result["max_observations"] == 30


def test_recent_exchange_sessions_ends_on_latest_exchange_session():
    sessions = recent_exchange_sessions(date(2026, 5, 17), count=3)

    assert sessions == [date(2026, 5, 13), date(2026, 5, 14), date(2026, 5, 15)]


def test_recent_window_progress_reports_remaining_requests_and_minimum_time():
    sessions = [date(2026, 5, 13), date(2026, 5, 14), date(2026, 5, 15)]
    result = recent_window_progress(
        asset_count=4,
        session_dates=sessions,
        covered_by_date={sessions[0]: 4, sessions[1]: 3, sessions[2]: 1},
        request_interval_seconds=15,
    )

    assert result["complete_sessions"] == 1
    assert result["covered_asset_sessions"] == 8
    assert result["expected_asset_sessions"] == 12
    assert result["remaining_requests_upper_bound"] == 4
    assert result["progress_ratio"] == 2 / 3
    assert result["theoretical_minimum_hours"] == 1 / 60
