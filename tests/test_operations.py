from app.analysis.market_calendar import exchange_calendar
from jobs.check_operations import contiguous_history_counts


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
